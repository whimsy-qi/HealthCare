import os
import hmac
import hashlib
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    size: int
    mime_type: str


class StorageError(RuntimeError):
    pass


class StorageService:
    def put_object(self, key: str, data: bytes, mime_type: str, bucket: Optional[str] = None) -> StoredObject:
        raise NotImplementedError

    def get_presigned_url(self, key: str, bucket: Optional[str] = None, expires_seconds: int = 3600) -> Optional[str]:
        raise NotImplementedError

    def delete_object(self, key: str, bucket: Optional[str] = None) -> bool:
        raise NotImplementedError

    def head_object(self, key: str, bucket: Optional[str] = None) -> Optional[dict]:
        raise NotImplementedError


class MinioStorageService(StorageService):
    def __init__(self):
        try:
            from minio import Minio
        except Exception as exc:
            raise StorageError("minio package is not installed") from exc

        endpoint = os.getenv("CHAT_MINIO_ENDPOINT") or os.getenv("MINIO_ENDPOINT") or "localhost:19028"
        access_key = os.getenv("CHAT_MINIO_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY") or "minioadmin"
        secret_key = os.getenv("CHAT_MINIO_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY") or "minioadmin"
        secure = (os.getenv("CHAT_MINIO_SECURE") or os.getenv("MINIO_SECURE") or "false").lower() == "true"

        self.bucket = os.getenv("CHAT_UPLOAD_BUCKET", "chat-uploads")
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    def _bucket(self, bucket: Optional[str]) -> str:
        return bucket or self.bucket

    def _ensure_bucket(self, bucket: str) -> None:
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)

    def put_object(self, key: str, data: bytes, mime_type: str, bucket: Optional[str] = None) -> StoredObject:
        import io

        target_bucket = self._bucket(bucket)
        self._ensure_bucket(target_bucket)
        self.client.put_object(
            bucket_name=target_bucket,
            object_name=key,
            data=io.BytesIO(data),
            length=len(data),
            content_type=mime_type,
        )
        return StoredObject(bucket=target_bucket, key=key, size=len(data), mime_type=mime_type)

    def get_presigned_url(self, key: str, bucket: Optional[str] = None, expires_seconds: int = 3600) -> Optional[str]:
        return self.client.presigned_get_object(
            bucket_name=self._bucket(bucket),
            object_name=key,
            expires=timedelta(seconds=expires_seconds),
        )

    def delete_object(self, key: str, bucket: Optional[str] = None) -> bool:
        self.client.remove_object(self._bucket(bucket), key)
        return True

    def head_object(self, key: str, bucket: Optional[str] = None) -> Optional[dict]:
        try:
            stat = self.client.stat_object(self._bucket(bucket), key)
            return {"size": stat.size, "etag": stat.etag, "content_type": stat.content_type}
        except Exception:
            return None


class LocalStorageService(StorageService):
    def __init__(self):
        backend_dir = Path(__file__).resolve().parents[1]
        root = Path(os.getenv("CHAT_LOCAL_OBJECT_ROOT", str(backend_dir / "object_storage"))).resolve()
        self.root = root
        self.bucket = os.getenv("CHAT_UPLOAD_BUCKET", "chat-uploads")
        self.public_base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

    def _bucket(self, bucket: Optional[str]) -> str:
        return bucket or self.bucket

    def _path(self, bucket: str, key: str) -> Path:
        safe_key = key.replace("\\", "/").lstrip("/")
        path = (self.root / bucket / safe_key).resolve()
        bucket_root = (self.root / bucket).resolve()
        try:
            path.relative_to(bucket_root)
        except ValueError:
            raise StorageError("invalid object key")
        return path

    def put_object(self, key: str, data: bytes, mime_type: str, bucket: Optional[str] = None) -> StoredObject:
        target_bucket = self._bucket(bucket)
        path = self._path(target_bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(bucket=target_bucket, key=key, size=len(data), mime_type=mime_type)

    def get_presigned_url(self, key: str, bucket: Optional[str] = None, expires_seconds: int = 3600) -> Optional[str]:
        target_bucket = self._bucket(bucket)
        encoded = "/".join(quote(part) for part in key.replace("\\", "/").split("/"))
        exp = int(time.time()) + max(60, int(expires_seconds))
        sig = sign_object_url(target_bucket, key, exp)
        return f"{self.public_base}/api/files/object/{quote(target_bucket)}/{encoded}?exp={exp}&sig={sig}"

    def delete_object(self, key: str, bucket: Optional[str] = None) -> bool:
        path = self._path(self._bucket(bucket), key)
        if path.exists():
            path.unlink()
        return True

    def head_object(self, key: str, bucket: Optional[str] = None) -> Optional[dict]:
        path = self._path(self._bucket(bucket), key)
        if not path.exists():
            return None
        return {"size": path.stat().st_size, "content_type": None}

    def local_path(self, bucket: str, key: str) -> Path:
        return self._path(bucket, key)


_STORAGE_SERVICE: Optional[StorageService] = None


def get_storage_service() -> StorageService:
    global _STORAGE_SERVICE
    if _STORAGE_SERVICE is not None:
        return _STORAGE_SERVICE

    backend = os.getenv("CHAT_STORAGE_BACKEND", "minio").lower()
    if backend == "local":
        _STORAGE_SERVICE = LocalStorageService()
        return _STORAGE_SERVICE

    try:
        _STORAGE_SERVICE = MinioStorageService()
    except Exception:
        if os.getenv("CHAT_STORAGE_ALLOW_LOCAL_FALLBACK", "true").lower() == "true":
            _STORAGE_SERVICE = LocalStorageService()
        else:
            raise
    return _STORAGE_SERVICE


def _signing_secret() -> str:
    return os.getenv("CHAT_UPLOAD_SIGNING_SECRET") or os.getenv("SECRET_KEY", "dev-storage-secret")


def sign_object_url(bucket: str, key: str, exp: int) -> str:
    payload = f"{bucket}\n{key}\n{exp}".encode("utf-8")
    secret = _signing_secret()
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_object_url_signature(bucket: str, key: str, exp: int, sig: str) -> bool:
    if int(exp) < int(time.time()):
        return False
    expected = sign_object_url(bucket, key, int(exp))
    return hmac.compare_digest(expected, sig or "")
