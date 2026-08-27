import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

from core.storage import LocalStorageService, StorageError, verify_object_url_signature


def test_local_storage_presigned_url_is_verifiable(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_LOCAL_OBJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("CHAT_UPLOAD_SIGNING_SECRET", "unit-test-secret")

    storage = LocalStorageService()
    storage.put_object("users/1/sessions/2/a.png", b"abc", "image/png", bucket="chat-uploads")

    url = storage.get_presigned_url("users/1/sessions/2/a.png", bucket="chat-uploads", expires_seconds=600)
    query = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))

    assert verify_object_url_signature(
        "chat-uploads",
        "users/1/sessions/2/a.png",
        int(query["exp"]),
        query["sig"],
    )


def test_local_storage_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_LOCAL_OBJECT_ROOT", str(tmp_path))

    storage = LocalStorageService()

    try:
        storage.put_object("../escape.png", b"abc", "image/png", bucket="chat-uploads")
    except StorageError:
        return

    raise AssertionError("path traversal object key should be rejected")
