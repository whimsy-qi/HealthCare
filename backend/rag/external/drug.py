from __future__ import annotations

from typing import Dict, List

import aiohttp


RXNAV_BASE = "https://rxnav.nlm.nih.gov/REST"
OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"
DAILYMED_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"


async def normalize_drug_name(name: str) -> Dict:
    name = (name or "").strip()
    if not name:
        return {}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{RXNAV_BASE}/rxcui.json",
            params={"name": name},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status != 200:
                return {"query": name}
            data = await resp.json()
    ids = data.get("idGroup", {}).get("rxnormId", [])
    return {"query": name, "rxcui": ids[0] if ids else None}


async def search_openfda_drug_label(name: str, top_k: int = 3) -> List[Dict]:
    name = (name or "").strip()
    if not name:
        return []
    async with aiohttp.ClientSession() as session:
        async with session.get(
            OPENFDA_LABEL,
            params={"search": f'openfda.generic_name:"{name}" OR openfda.brand_name:"{name}"', "limit": str(top_k)},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    return data.get("results", [])


async def search_dailymed_spls(name: str, top_k: int = 3) -> List[Dict]:
    name = (name or "").strip()
    if not name:
        return []
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{DAILYMED_BASE}/spls.json",
            params={"drug_name": name, "pagesize": str(max(1, min(top_k, 10)))},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    return data.get("data", [])


def openfda_label_sections(label: Dict) -> List[Dict]:
    openfda = label.get("openfda") or {}
    generic = ", ".join(openfda.get("generic_name") or [])
    brand = ", ".join(openfda.get("brand_name") or [])
    title = brand or generic or label.get("set_id") or "drug label"
    sections = {
        "indications_and_usage": label.get("indications_and_usage"),
        "contraindications": label.get("contraindications"),
        "warnings": label.get("warnings") or label.get("boxed_warning"),
        "adverse_reactions": label.get("adverse_reactions"),
        "drug_interactions": label.get("drug_interactions"),
        "dosage_and_administration": label.get("dosage_and_administration"),
        "use_in_specific_populations": label.get("use_in_specific_populations"),
    }
    out: List[Dict] = []
    for section, values in sections.items():
        if not values:
            continue
        text = "\n".join(str(v).strip() for v in values if str(v).strip())
        if text:
            out.append({
                "title": title,
                "section_title": section,
                "text": text,
                "set_id": label.get("set_id", ""),
                "source_url": "https://open.fda.gov/apis/drug/label/",
                "generic_name": generic,
                "brand_name": brand,
            })
    return out
