from __future__ import annotations

from typing import Dict, List

import aiohttp


CTGOV_STUDIES = "https://clinicaltrials.gov/api/v2/studies"


async def search_clinical_trials(query: str, top_k: int = 5) -> List[Dict]:
    query = (query or "").strip()
    if not query:
        return []
    async with aiohttp.ClientSession() as session:
        async with session.get(
            CTGOV_STUDIES,
            params={"query.term": query, "pageSize": str(max(1, min(top_k, 100)))},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    return data.get("studies", [])
