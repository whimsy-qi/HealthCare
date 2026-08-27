from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from rag.config import BACKEND_ROOT


DEFAULT_STATE_FILE = BACKEND_ROOT / "rag" / "ingest_state" / "local_drug_ingest_state.jsonl"

COMMON_ALIASES = {
    "拜阿司匹灵": ["阿司匹林", "阿司匹林肠溶片", "aspirin"],
    "aspirin": ["阿司匹林", "阿司匹林肠溶片", "拜阿司匹灵"],
    "metformin": ["二甲双胍", "盐酸二甲双胍", "二甲双胍片"],
    "ibuprofen": ["布洛芬", "布洛芬片", "布洛芬缓释胶囊"],
    "warfarin": ["华法林", "华法林钠"],
    "clarithromycin": ["克拉霉素"],
    "atorvastatin": ["阿托伐他汀", "阿托伐他汀钙"],
    "simvastatin": ["辛伐他汀"],
    "omeprazole": ["奥美拉唑"],
    "levofloxacin": ["左氧氟沙星"],
    "amoxicillin": ["阿莫西林"],
}

DOSAGE_FORM_SUFFIXES = [
    "肠溶片",
    "缓释片",
    "分散片",
    "咀嚼片",
    "胶囊",
    "软胶囊",
    "颗粒",
    "注射液",
    "注射剂",
    "片",
    "丸",
    "散",
    "膏",
    "乳膏",
    "滴眼液",
    "口服液",
]


def _iter_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def strip_dosage_form(name: str) -> str:
    cleaned = re.sub(r"\s+", "", name or "")
    cleaned = re.sub(r"[（(].*?[）)]", "", cleaned)
    for suffix in sorted(DOSAGE_FORM_SUFFIXES, key=len, reverse=True):
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 1:
            return cleaned[: -len(suffix)]
    return cleaned


@lru_cache(maxsize=1)
def load_local_drug_aliases(state_file: str = str(DEFAULT_STATE_FILE)) -> dict[str, list[str]]:
    aliases: dict[str, set[str]] = {key: set(values) for key, values in COMMON_ALIASES.items()}
    for key, values in COMMON_ALIASES.items():
        for value in values:
            aliases.setdefault(value, set()).add(key)
            aliases[value].update(v for v in values if v != value)
    path = Path(state_file)
    for row in _iter_jsonl(path):
        if row.get("status") != "completed":
            continue
        drug_name = str(row.get("drug_name") or "").strip()
        if not drug_name:
            continue
        base_name = strip_dosage_form(drug_name)
        candidates = {drug_name}
        if base_name and base_name != drug_name:
            candidates.add(base_name)
        for candidate in list(candidates):
            aliases.setdefault(candidate, set()).update(candidates - {candidate})
    return {key: sorted(values) for key, values in aliases.items() if values}


def expand_drug_query(query: str, *, max_aliases: int = 8, state_file: str = str(DEFAULT_STATE_FILE)) -> tuple[str, list[str]]:
    q = query or ""
    if not q:
        return q, []
    aliases = load_local_drug_aliases(state_file)
    matched: list[str] = []
    lower_q = q.lower()
    for key, values in aliases.items():
        if key and (key in q or key.lower() in lower_q):
            for value in values:
                if value and value not in q and value not in matched:
                    matched.append(value)
                    if len(matched) >= max_aliases:
                        break
        if len(matched) >= max_aliases:
            break
    if not matched:
        return q, []
    return f"{q} {' '.join(matched)}"[:700], matched
