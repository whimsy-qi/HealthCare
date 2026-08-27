from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from rag.config import DEFAULT_MANIFEST


REQUIRED_FIELDS = {
    "source_id",
    "name",
    "source_type",
    "authority_tier",
    "license",
    "url",
    "department",
    "language",
    "refresh_policy",
    "allowed_ingest_mode",
}


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    name: str
    source_type: str
    authority_tier: str
    license: str
    url: str
    department: str
    language: str
    refresh_policy: str
    allowed_ingest_mode: str


class SourceRegistry:
    def __init__(self, records: Iterable[SourceRecord]):
        self._records = {r.source_id: r for r in records}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SourceRegistry":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        records: List[SourceRecord] = []
        for raw in data.get("sources", []):
            missing = REQUIRED_FIELDS - set(raw)
            if missing:
                raise ValueError(f"source {raw.get('source_id', '<unknown>')} missing fields: {sorted(missing)}")
            records.append(SourceRecord(**{k: raw[k] for k in REQUIRED_FIELDS}))
        return cls(records)

    def get(self, source_id: str) -> Optional[SourceRecord]:
        return self._records.get(source_id)

    def require(self, source_id: str) -> SourceRecord:
        record = self.get(source_id)
        if record is None:
            raise KeyError(f"source_id is not registered in manifest: {source_id}")
        return record

    def by_type(self, source_type: str) -> List[SourceRecord]:
        return [r for r in self._records.values() if r.source_type == source_type]

    def as_dict(self) -> Dict[str, Any]:
        return {k: vars(v) for k, v in self._records.items()}


def load_default_registry() -> SourceRegistry:
    return SourceRegistry.from_yaml(DEFAULT_MANIFEST)
