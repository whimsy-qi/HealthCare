from __future__ import annotations

from typing import Dict, List
import xml.etree.ElementTree as ET

import aiohttp


PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


async def search_pubmed_abstracts(query: str, top_k: int = 5) -> List[Dict]:
    query = (query or "").strip()
    if not query:
        return []
    top_k = max(1, min(int(top_k), 20))
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{PUBMED_BASE}/esearch.fcgi",
            params={"db": "pubmed", "term": query, "retmax": str(top_k), "retmode": "json", "sort": "relevance"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        pmids = data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []
        async with session.get(
            f"{PUBMED_BASE}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", "rettype": "abstract"},
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            xml_text = await resp.text()
    root = ET.fromstring(xml_text)
    hits: List[Dict] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID") or ""
        title = "".join(article.findtext(".//ArticleTitle") or "").strip()
        abstract = " ".join((el.text or "").strip() for el in article.findall(".//AbstractText") if el.text)
        if pmid:
            hits.append({"pmid": pmid, "title": title, "abstract": abstract})
    return hits
