"""
MADDx 工具层（D8）
================
给辩论 agent（Proposer / Critic / Defender）提供受控的证据检索工具。

设计要点：
  1. **统一调用入口**：ToolRegistry.invoke(tool_name, args, caller, round_idx, bb)
     - 每次调用自动在 Blackboard 留下 tool_call + tool_result 两条记录，
       形成 DAG，前端可直接绘制"谁在哪一轮查了什么"。
  2. **会话级缓存**：(tool, hashed_args) → ToolResult
     - 防止 agent 在同一 session 反复查同一组合；
     - cached=True 的结果不计入 "新证据"（Rule 4 NO_NEW_EVIDENCE 用）。
  3. **Timeout + Fallback**：每个工具硬超时 8s，失败返回空 hits，不抛。
  4. **Schema 安全**：工具入参走 pydantic / 手动 validate，agent 胡乱传参时
     返回 `{"error": "..."}` 而非崩溃。

三个内置工具：
  - kg_query  : Neo4j 图谱检索（疾病↔症状 / 药物↔疾病）
  - rag_search: 本地指南向量检索（DashVector，过滤 drug_manual 之外的文档）
  - web_search: Tavily 权威医学站群检索（仅限 AUTHORITATIVE+GENERAL 域）
"""
import os
import re
import json
import hashlib
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Awaitable

from dotenv import load_dotenv, find_dotenv

from core.blackboard import Blackboard
from core.blackboard_schema import ToolCall, ToolResult, ToolHit
from core.sse_emitter import emit as sse_emit

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("MADDx.Tools")

TOOL_TIMEOUT_SECONDS = 8
MAX_HITS_PER_CALL = 10


# =======================================================================
# 外部服务 Lazy-init（复用 medication_agent 的惰性连接策略，避免 import 崩）
# =======================================================================

_neo4j_driver = None
_dv_client = None
_collection = None


def _get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        from neo4j import GraphDatabase

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        _neo4j_driver = GraphDatabase.driver(uri, auth=(user, password))
    return _neo4j_driver


def _get_dv_collection():
    raise RuntimeError("DashVector fallback is disabled; use RAG_BACKEND=medical_graphrag")


# =======================================================================
# 1. KG Tool：Neo4j 图谱检索
# =======================================================================

# 支持的查询模式（受控白名单，防止 agent 注入任意 Cypher）
KG_MODES = {
    "disease_symptoms":        # 给疾病 → 拉典型症状
        """
        MATCH (d:Disease)-[:HAS_SYMPTOM]->(s:Symptom)
        WHERE d.name = $name OR d.name CONTAINS $name OR $name CONTAINS d.name
        RETURN d.name AS disease, s.name AS symptom
        LIMIT $limit
        """,
    "symptom_diseases":        # 给症状 → 反查可能疾病
        """
        MATCH (d:Disease)-[:HAS_SYMPTOM]->(s:Symptom)
        WHERE s.name = $name OR s.name CONTAINS $name OR $name CONTAINS s.name
        RETURN d.name AS disease, s.name AS symptom
        LIMIT $limit
        """,
    "disease_department":      # 疾病 → 所属科室
        """
        MATCH (d:Disease)-[:BELONGS_TO]->(dp:Department)
        WHERE d.name = $name OR d.name CONTAINS $name OR $name CONTAINS d.name
        RETURN d.name AS disease, dp.name AS department
        LIMIT $limit
        """,
    "disease_drugs":           # 疾病 → 治疗药物
        """
        MATCH (m:Drug)-[:TREATS]->(d:Disease)
        WHERE d.name = $name OR d.name CONTAINS $name OR $name CONTAINS d.name
        RETURN d.name AS disease, m.name AS drug
        LIMIT $limit
        """,
    "drug_contraindications":  # 药物 → 禁忌疾病 / 症状
        """
        MATCH (m:Drug)-[:CONTRAINDICATED_FOR]->(c)
        WHERE m.name = $name OR m.name CONTAINS $name OR $name CONTAINS m.name
        RETURN m.name AS drug, c.name AS contraindication, labels(c)[0] AS ctype
        LIMIT $limit
        """,
}


async def query_symptom_disease_edges(
    symptom_names: List[str],
    limit_per_symptom: int = 30,
) -> Dict[str, Any]:
    """
    Deterministic KG helper for MedRAG-style disease priors.

    This is intentionally not exposed to LLM tool schemas. It only runs a
    fixed Disease-HAS_SYMPTOM-Symptom pattern and returns enough statistics for
    an IDF/BM25-like ranker.
    """
    names = []
    seen = set()
    for raw in symptom_names or []:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)

    if not names:
        return {
            "total_diseases": 0,
            "symptoms": [],
            "fallback": True,
            "error": "empty_symptom_names",
        }

    limit = max(1, min(int(limit_per_symptom or 30), 100))

    total_cypher = "MATCH (d:Disease) RETURN count(DISTINCT d) AS total"
    df_cypher = """
        MATCH (d:Disease)-[:HAS_SYMPTOM]->(s:Symptom)
        WHERE s.name = $name OR s.name CONTAINS $name OR $name CONTAINS s.name
        RETURN count(DISTINCT d) AS df
        """
    edge_cypher = """
        MATCH (d:Disease)-[:HAS_SYMPTOM]->(s:Symptom)
        WHERE s.name = $name OR s.name CONTAINS $name OR $name CONTAINS s.name
        RETURN d.name AS disease,
               s.name AS symptom,
               CASE
                 WHEN s.name = $name THEN 1.0
                 ELSE 0.8
               END AS match_quality
        LIMIT $limit
        """

    def _run_sync() -> Dict[str, Any]:
        with _get_neo4j_driver().session() as session:
            total_row = session.run(total_cypher).single()
            total = int(total_row["total"] or 0) if total_row else 0
            symptom_blocks = []
            for name in names:
                df_row = session.run(df_cypher, name=name).single()
                df = int(df_row["df"] or 0) if df_row else 0
                rows = session.run(edge_cypher, name=name, limit=limit)
                edges = []
                for row in rows:
                    disease = str(row["disease"] or "").strip()
                    matched_symptom = str(row["symptom"] or "").strip()
                    if not disease or not matched_symptom:
                        continue
                    edges.append({
                        "disease": disease,
                        "matched_symptom": matched_symptom,
                        "match_quality": float(row["match_quality"] or 0.8),
                        "ref": f"kg:Disease:{disease}:HAS_SYMPTOM:{matched_symptom}",
                    })
                symptom_blocks.append({
                    "input_symptom": name,
                    "df": df,
                    "edges": edges,
                })
            return {
                "total_diseases": total,
                "symptoms": symptom_blocks,
                "fallback": False,
                "error": "",
            }

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_sync),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("[MedRAGRanker] KG query timeout for %s", names[:5])
        return {
            "total_diseases": 0,
            "symptoms": [],
            "fallback": True,
            "error": "timeout",
        }
    except Exception as e:
        logger.warning("[MedRAGRanker] KG query failed: %s", e)
        return {
            "total_diseases": 0,
            "symptoms": [],
            "fallback": True,
            "error": type(e).__name__,
        }


async def _kg_query_impl(args: dict) -> List[ToolHit]:
    """
    args = {
        "mode": "disease_symptoms" | "symptom_diseases" | ...,
        "name": <string>,
        "limit": <int, default 8>
    }
    """
    mode = args.get("mode")
    name = (args.get("name") or "").strip()
    limit = int(args.get("limit") or 8)
    if mode not in KG_MODES:
        return [{"ref": "error", "text": f"unsupported kg_query mode: {mode}"}]
    if not name:
        return [{"ref": "error", "text": "kg_query.name is required"}]
    limit = max(1, min(limit, MAX_HITS_PER_CALL))

    cypher = KG_MODES[mode]

    def _run_sync():
        with _get_neo4j_driver().session() as session:
            result = session.run(cypher, name=name, limit=limit)
            return [dict(r) for r in result]

    try:
        rows = await asyncio.wait_for(asyncio.to_thread(_run_sync), timeout=TOOL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(f"[KGTool] timeout mode={mode} name={name}")
        return []
    except Exception as e:
        logger.error(f"[KGTool] error mode={mode} name={name}: {e}")
        return []

    hits: List[ToolHit] = []
    for row in rows:
        # 根据 mode 提取 subject/predicate/object
        if mode == "disease_symptoms":
            hit = {"ref": f"kg:Disease:{row['disease']}:HAS_SYMPTOM:{row['symptom']}",
                   "subject": row["disease"], "predicate": "HAS_SYMPTOM", "object": row["symptom"]}
        elif mode == "symptom_diseases":
            hit = {"ref": f"kg:Disease:{row['disease']}:HAS_SYMPTOM:{row['symptom']}",
                   "subject": row["disease"], "predicate": "HAS_SYMPTOM", "object": row["symptom"]}
        elif mode == "disease_department":
            hit = {"ref": f"kg:Disease:{row['disease']}:BELONGS_TO:{row['department']}",
                   "subject": row["disease"], "predicate": "BELONGS_TO", "object": row["department"]}
        elif mode == "disease_drugs":
            hit = {"ref": f"kg:Drug:{row['drug']}:TREATS:{row['disease']}",
                   "subject": row["drug"], "predicate": "TREATS", "object": row["disease"]}
        elif mode == "drug_contraindications":
            hit = {"ref": f"kg:Drug:{row['drug']}:CONTRAINDICATED_FOR:{row['contraindication']}",
                   "subject": row["drug"], "predicate": "CONTRAINDICATED_FOR",
                   "object": f"{row['contraindication']}({row.get('ctype','')})"}
        else:
            continue
        hits.append(hit)
    return hits


# =======================================================================
# 2. RAG Tool：本地指南向量检索
# =======================================================================

async def _rag_search_impl(args: dict) -> List[ToolHit]:
    """
    args = {
        "query": <string>,
        "top_k": <int, default 5>,
        "source_filter": <Optional[str]>,  # 如 "clinical_guideline"
    }
    """
    query = (args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 5)
    source_filter = args.get("source_filter")
    if not query:
        return [{"ref": "error", "text": "rag_search.query is required"}]
    top_k = max(1, min(top_k, MAX_HITS_PER_CALL))

    try:
        from rag.service import retrieve_medical_evidence

        result = await retrieve_medical_evidence(query, intent="guideline_qa", top_k=top_k)
        if result.items:
            return [
                {
                    "ref": item.to_ref()["ref_id"],
                    "text": item.text[:400],
                    "source": f"{item.source_tier}/{item.source_type}/{item.title}",
                    "score": float(item.scores.get("rerank", 0.0)),
                    "page": item.page_start,
                    "section": item.section_title,
                }
                for item in result.items
            ]
    except Exception as e:
        logger.warning(f"[RAGTool] RAG v2 failed; DashVector fallback is disabled: {e}")
        return []

    logger.warning("[RAGTool] RAG v2 returned no evidence; DashVector fallback is disabled")
    return []

    def _run_sync():
        import dashscope

        dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
        resp = dashscope.MultiModalEmbedding.call(
            model="qwen3-vl-embedding",
            input=[{"text": f"医学检索：{query}"}],
        )
        if resp.status_code != 200:
            raise RuntimeError(f"embedding failed: {resp}")
        vec = resp.output["embeddings"][0]["embedding"]
        flt = f"source = '{source_filter}'" if source_filter else None
        return _get_dv_collection().query(vector=vec, topk=top_k, filter=flt)

    try:
        res = await asyncio.wait_for(asyncio.to_thread(_run_sync), timeout=TOOL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(f"[RAGTool] timeout query={query[:40]}")
        return []
    except Exception as e:
        logger.error(f"[RAGTool] error query={query[:40]}: {e}")
        return []

    hits: List[ToolHit] = []
    if res and res.output:
        for i, doc in enumerate(res.output):
            content = doc.fields.get("content", "") if hasattr(doc, "fields") else ""
            src = doc.fields.get("source", "unknown") if hasattr(doc, "fields") else "unknown"
            title = doc.fields.get("drug_name") or doc.fields.get("title") or ""
            hits.append({
                "ref": f"rag:chunk_{doc.id}" if hasattr(doc, "id") else f"rag:chunk_{i}",
                "text": content[:400],
                "source": f"{src}/{title}" if title else src,
                "score": float(doc.score) if hasattr(doc, "score") else 0.0,
            })
    return hits


# =======================================================================
# 3. Web Search Tool：Tavily 权威站群
# =======================================================================

async def _web_search_impl(args: dict) -> List[ToolHit]:
    """
    args = {
        "query": <string>,
        "top_k": <int, default 3>,
    }
    默认只检索 AUTHORITATIVE + GENERAL 域（chinacdc / nhc / nih / 丁香园 等）。
    """
    query = (args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 3)
    if not query:
        return [{"ref": "error", "text": "web_search.query is required"}]
    top_k = max(1, min(top_k, 5))   # Web 搜索更贵，上限更严

    # 复用已有的 tavily + rerank 管线
    from scripts.tools_search import search_dynamic_medical_info

    try:
        _context, sources = await asyncio.wait_for(
            search_dynamic_medical_info(
                query=query,
                raw_fetch_count=6,
                final_top_k=top_k,
                force_domain="AUTHORITATIVE,GENERAL",
            ),
            timeout=TOOL_TIMEOUT_SECONDS + 4,   # 公网更慢
        )
    except asyncio.TimeoutError:
        logger.warning(f"[WebSearch] timeout query={query[:40]}")
        return []
    except Exception as e:
        logger.error(f"[WebSearch] error query={query[:40]}: {e}")
        return []

    hits: List[ToolHit] = []
    for s in sources or []:
        title = (s.get("title") or "").strip()
        content = (s.get("content") or "").strip()
        url = (s.get("url") or "unknown").strip()
        hits.append({
            "ref": f"web:{url}",
            "title": title[:160],
            "text": (content or title)[:400],
            "content": content[:800],
            "source": url,
            "url": url,
            "score": float(s.get("score", 0.0)),
        })
    return hits


# =======================================================================
# Registry + Invoke（统一入口）
# =======================================================================

# ═══════════════════════════════════════════════════════
# 4. PubMed Tool：学术文献检索
# ═══════════════════════════════════════════════════════

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


async def _pubmed_search_impl(args: dict) -> List[ToolHit]:
    """
    args = {
        "query": <string>,     # PubMed 检索词
        "top_k": <int, default 3>,
    }
    使用 NCBI Entrez API（免费，无需 API Key）检索 PubMed。
    """
    import aiohttp
    query = (args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 3)
    top_k = max(1, min(top_k, 5))
    if not query:
        return [{"ref": "error", "text": "pubmed_search.query is required"}]

    try:
        async with aiohttp.ClientSession() as session:
            # Step 1: 搜索 PMID 列表
            search_url = f"{PUBMED_BASE}/esearch.fcgi"
            params = {
                "db": "pubmed", "term": query, "retmax": str(top_k),
                "retmode": "json", "sort": "relevance",
            }
            async with session.get(search_url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return [{"ref": "error", "text": f"PubMed search failed: HTTP {resp.status}"}]
                data = await resp.json()
            pmids = data.get("esearchresult", {}).get("idlist", [])
            if not pmids:
                return [{"ref": "pubmed:no_results", "text": f"PubMed 未找到与 '{query[:60]}' 相关的结果"}]

            # Step 2: 获取摘要
            fetch_url = f"{PUBMED_BASE}/efetch.fcgi"
            fetch_params = {
                "db": "pubmed", "id": ",".join(pmids),
                "retmode": "xml", "rettype": "abstract",
            }
            async with session.get(fetch_url, params=fetch_params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                xml_text = await resp.text()

            # Step 3: 解析 XML 提取 title + abstract + PMID
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            hits: List[ToolHit] = []
            for article in root.findall(".//PubmedArticle"):
                pmid_el = article.find(".//PMID")
                pmid = pmid_el.text if pmid_el is not None else "?"
                title_el = article.find(".//ArticleTitle")
                title = title_el.text if title_el is not None else "无标题"
                abstract_el = article.find(".//AbstractText")
                abstract = abstract_el.text if abstract_el is not None else ""
                # 如果有多个 AbstractText，拼接
                if not abstract:
                    parts = [a.text or "" for a in article.findall(".//AbstractText")]
                    abstract = " ".join(parts)
                hits.append({
                    "ref": f"pubmed:{pmid}",
                    "title": title[:120],
                    "text": abstract[:400],
                    "source": f"PubMed PMID:{pmid}",
                    "score": 1.0,
                })
            return hits[:top_k]
    except Exception as e:
        logger.error(f"[PubMedTool] error: {e}")
        return [{"ref": "error", "text": f"PubMed 检索异常: {str(e)[:80]}"}]


# ═══════════════════════════════════════════════════════
# 5. Social Search Tool：中文社交平台检索
# ═══════════════════════════════════════════════════════

SOCIAL_SOURCES = {
    "wechat": {"name": "微信公众号", "desc": "中文医疗科普/辟谣文章"},
    "xiaohongshu": {"name": "小红书", "desc": "用户分享的健康经验与产品口碑"},
    "bilibili": {"name": "B站", "desc": "医学科普视频字幕"},
    "web": {"name": "通用网页", "desc": "通过 Jina Reader 清洗后的网页全文"},
}


def _is_probable_image_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    image_markers = (
        "xhscdn.com", "sns-webpic", "sns-img", "sns-avatar",
        "imageview2", "format/webp", "spectrum/"
    )
    if any(marker in lowered for marker in image_markers):
        return True
    return bool(re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|#|$)", lowered))


def _normalize_xhs_open_url(raw_url: str = "", note_id: str = "", query: str = "") -> tuple[str, str]:
    """Return (open_url, open_url_type) for XHS without using image/CDN URLs."""
    from urllib.parse import quote

    url = (raw_url or "").strip().strip('"').strip("'")
    note_id = (note_id or "").strip().strip('"').strip("'")

    if url.startswith(("http://", "https://")) and not _is_probable_image_url(url):
        lowered = url.lower()
        if "xiaohongshu.com" in lowered or "xhslink.com" in lowered:
            return url, "post"

    if note_id and re.fullmatch(r"[A-Za-z0-9_-]{8,40}", note_id):
        return f"https://www.xiaohongshu.com/explore/{note_id}", "post"

    return f"https://www.xiaohongshu.com/search_result?keyword={quote(query)}", "search"


def _extract_xhs_field_values(raw: str, *field_names: str) -> List[str]:
    values: List[str] = []
    for field in field_names:
        pattern = rf"^\s*{re.escape(field)}:\s*(.+?)\s*$"
        values.extend(
            v.strip().strip('"').strip("'")
            for v in re.findall(pattern, raw or "", flags=re.MULTILINE)
            if v.strip()
        )
    return values


_XHS_COOKIE_CONFIG_LOGGED = False


def _read_xhs_cookie() -> str:
    cookie = (os.getenv("XHS_COOKIE") or "").strip()
    if cookie:
        return cookie

    cookie_file = (os.getenv("XHS_COOKIE_FILE") or "").strip()
    if not cookie_file:
        return ""
    try:
        path = Path(cookie_file).expanduser()
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning(f"[SocialTool/xhs] failed to read XHS_COOKIE_FILE: {type(e).__name__}")
        return ""


def _cookie_header_to_dict(cookie: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value
    return cookies


def _xhs_cli_home() -> Path:
    return Path(os.getenv("XHS_CLI_HOME") or (Path.cwd() / "secrets" / "xhs-cli-home")).expanduser()


def _persist_xhs_cookie_for_cli(cookie: str) -> None:
    if not cookie:
        return
    try:
        import time

        cookies = _cookie_header_to_dict(cookie)
        if cookies.get("a1"):
            config_dir = _xhs_cli_home() / ".xiaohongshu-cli"
            config_dir.mkdir(parents=True, exist_ok=True)
            cookie_path = config_dir / "cookies.json"
            cookie_path.write_text(
                json.dumps({**cookies, "saved_at": time.time()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception as e:
        logger.warning(f"[SocialTool/xhs] failed to prepare xhs CLI cookie: {type(e).__name__}")


def _xhs_env() -> Dict[str, str]:
    global _XHS_COOKIE_CONFIG_LOGGED
    cookie = _read_xhs_cookie()
    if not _XHS_COOKIE_CONFIG_LOGGED:
        logger.info(f"[SocialTool/xhs] XHS_COOKIE configured={bool(cookie)}")
        _XHS_COOKIE_CONFIG_LOGGED = True
    _persist_xhs_cookie_for_cli(cookie)

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if cookie or os.getenv("XHS_CLI_HOME"):
        cli_home = str(_xhs_cli_home())
        env["USERPROFILE"] = cli_home
        env["HOME"] = cli_home
    if cookie:
        env["XHS_COOKIE"] = cookie
        env.setdefault("XIAOHONGSHU_COOKIE", cookie)
        env.setdefault("REDNOTE_COOKIE", cookie)
    return env


async def _run_xhs_command(xhs_bin: str, args: List[str], timeout: int) -> tuple[int, str, str]:
    command = [sys.executable, "-m", "xhs_cli"] if xhs_bin == "__module__" else [xhs_bin]
    try:
        import subprocess

        completed = await asyncio.to_thread(
            subprocess.run,
            [*command, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_xhs_env(),
        )
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    return (int(completed.returncode or 0), completed.stdout or "", completed.stderr or "")


def _find_xhs_binary() -> str:
    import shutil

    found = shutil.which("xhs")
    if found:
        return found
    candidate = Path(sys.executable).with_name("xhs.exe")
    if candidate.exists():
        return "__module__"
    candidate = Path(sys.executable).with_name("xhs")
    if candidate.exists():
        return "__module__"
    return ""


def _extract_xhs_multiline_field(raw: str, *field_names: str) -> str:
    for field in field_names:
        pattern = rf"^\s*{re.escape(field)}:\s*(.*?)(?=^\s*[A-Za-z_][\w-]*:\s*|\Z)"
        match = re.search(pattern, raw or "", flags=re.MULTILINE | re.DOTALL)
        if match:
            value = match.group(1).strip().strip('"').strip("'")
            if value:
                return value
    return ""


def _iter_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _record_value(obj: Any, *keys: str) -> str:
    if isinstance(obj, list):
        for item in obj:
            value = _record_value(item, *keys)
            if value:
                return value
        return ""
    if not isinstance(obj, dict):
        return ""

    lower = {str(k).lower(): v for k, v in obj.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            nested = _record_value(value, *keys)
            if nested:
                return nested
            continue
        text = str(value).strip()
        if text:
            return text

    for value in obj.values():
        nested = _record_value(value, *keys)
        if nested:
            return nested
    return ""


def _json_from_xhs_output(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    starts = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
    if not starts:
        return None
    start = min(starts)
    for end in range(len(text), start, -1):
        try:
            return json.loads(text[start:end].strip())
        except Exception:
            continue
    return None


def _sanitize_xhs_text(text: str, limit: int = 1800) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    sensitive_markers = (
        "cookie", "web_session", "id_token", "xsec", "acw_tc",
        "websectiga", "sec_poison_id", "a1=", "gid=",
    )
    lines = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in sensitive_markers):
            continue
        lines.append(line.strip())
    cleaned = "\n".join(line for line in lines if line)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:limit]


def _extract_xhs_candidates(raw: str, query: str, top_k: int) -> List[Dict[str, str]]:
    candidates: List[Dict[str, str]] = []
    seen = set()

    data = _json_from_xhs_output(raw)
    if data is not None:
        for record in _iter_dicts(data):
            title = _record_value(record, "display_title", "title", "note_title", "desc", "description")
            note_id = _record_value(record, "note_id", "noteId", "id", "noteIdStr")
            raw_url = _record_value(record, "share_link", "note_url", "web_url", "xhs_url", "link", "url")
            if not (title or note_id or raw_url):
                continue
            open_url, open_url_type = _normalize_xhs_open_url(raw_url, note_id, query)
            read_target = raw_url if raw_url and not _is_probable_image_url(raw_url) else note_id
            key = open_url or read_target or title
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "title": title or f"小红书搜索结果：{query[:60]}",
                "note_id": note_id,
                "raw_url": raw_url,
                "open_url": open_url,
                "open_url_type": open_url_type,
                "read_target": read_target,
            })
            if len(candidates) >= top_k:
                return candidates

    titles = _extract_xhs_field_values(raw, "display_title", "title", "note_title")
    note_ids = _extract_xhs_field_values(raw, "note_id", "noteId", "id")
    raw_urls = _extract_xhs_field_values(raw, "share_link", "note_url", "web_url", "xhs_url", "link", "url")
    for i, title in enumerate(t for t in titles if t):
        raw_url = raw_urls[i] if i < len(raw_urls) else ""
        note_id = note_ids[i] if i < len(note_ids) else ""
        open_url, open_url_type = _normalize_xhs_open_url(raw_url, note_id, query)
        read_target = raw_url if raw_url and not _is_probable_image_url(raw_url) else note_id
        key = open_url or read_target or title
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "title": title,
            "note_id": note_id,
            "raw_url": raw_url,
            "open_url": open_url,
            "open_url_type": open_url_type,
            "read_target": read_target,
        })
        if len(candidates) >= top_k:
            break
    return candidates


def _extract_xhs_read_payload(raw: str) -> Dict[str, str]:
    data = _json_from_xhs_output(raw)
    if data is not None:
        for record in _iter_dicts(data):
            body = _record_value(
                record,
                "content", "desc", "description", "note_desc",
                "noteContent", "text", "body",
            )
            if body:
                return {
                    "title": _record_value(record, "display_title", "title", "note_title"),
                    "post_body": _sanitize_xhs_text(body),
                    "url": _record_value(record, "share_link", "note_url", "web_url", "xhs_url", "link", "url"),
                }

    title = _extract_xhs_multiline_field(raw, "display_title", "title", "note_title")
    body = _extract_xhs_multiline_field(raw, "content", "desc", "description", "note_desc", "text", "body")
    if not body:
        raw_clean = _sanitize_xhs_text(raw)
        if len(raw_clean) >= 80 and "error" not in raw_clean[:120].lower():
            body = raw_clean
    return {
        "title": title,
        "post_body": _sanitize_xhs_text(body),
        "url": (_extract_xhs_field_values(raw, "share_link", "note_url", "web_url", "xhs_url", "link", "url") or [""])[0],
    }


async def _read_xhs_post(xhs_bin: str, read_target: str) -> Dict[str, str]:
    if not read_target or "search_result" in read_target:
        return {}
    code, stdout, _stderr = await _run_xhs_command(
        xhs_bin,
        ["read", read_target],
        timeout=TOOL_TIMEOUT_SECONDS + 8,
    )
    if code != 0 or not stdout.strip():
        return {}
    payload = _extract_xhs_read_payload(stdout)
    if not payload.get("post_body"):
        return {}
    return payload


def _source_label_from_ugc_url(url: str) -> str:
    from urllib.parse import urlparse

    host = urlparse(url or "").netloc.lower().replace("www.", "")
    if "xiaohongshu.com" in host:
        return "小红书"
    if "zhihu.com" in host:
        return "知乎"
    if "weibo.com" in host:
        return "微博"
    if "douban.com" in host:
        return "豆瓣"
    if "bilibili.com" in host:
        return "B站"
    if "tieba.baidu.com" in host:
        return "百度贴吧"
    return host or "UGC"


async def _tavily_ugc_search(query: str, top_k: int) -> List[ToolHit]:
    if top_k <= 0 or not os.getenv("TAVILY_API_KEY"):
        return []
    try:
        from tavily import AsyncTavilyClient
        from scripts.tools_search import DOMAIN_MAP, is_safe_content

        client = AsyncTavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
        response = await asyncio.wait_for(
            client.search(
                query=query,
                search_depth="basic",
                max_results=max(3, top_k),
                include_domains=DOMAIN_MAP.get("UGC", []),
                include_raw_content="text",
            ),
            timeout=TOOL_TIMEOUT_SECONDS + 8,
        )
        hits: List[ToolHit] = []
        for res in response.get("results", []) or []:
            title = (res.get("title") or "").strip()
            content = (res.get("raw_content") or res.get("content") or "").strip()
            url = (res.get("url") or "").strip()
            if not (title or content or url):
                continue
            if not (is_safe_content(title) and is_safe_content(content)):
                continue
            platform = _source_label_from_ugc_url(url)
            status = "raw_content" if res.get("raw_content") else "snippet"
            hits.append({
                "ref": f"ugc:{url or abs(hash(title + content))}",
                "title": title[:160] or f"{platform} 舆情线索",
                "text": content[:500] or title[:300],
                "content": content[:1200] or title[:300],
                "source": platform,
                "platform": platform,
                "url": url,
                "open_url": "" if _is_probable_image_url(url) else url,
                "open_url_type": "web" if url and not _is_probable_image_url(url) else "none",
                "score": float(res.get("score") or 0.45),
                "evidence_type": "social_opinion",
                "content_available": bool(content),
                "content_status": status,
                "fetch_method": "tavily_ugc",
                "summary_note": status,
                "evidence_limit": "Tavily UGC 结果来自公开网页检索，只能作为舆情线索；无法保证等同于平台原帖完整正文。",
            })
            if len(hits) >= top_k:
                break
        return hits
    except asyncio.TimeoutError:
        logger.warning(f"[SocialTool/ugc] timeout query={query[:40]}")
        return []
    except Exception as e:
        logger.warning(f"[SocialTool/ugc] error: {type(e).__name__}")
        return []


def _dedupe_social_hits(hits: List[ToolHit], limit: int) -> List[ToolHit]:
    merged: Dict[str, ToolHit] = {}
    for hit in hits:
        key = str(hit.get("open_url") or hit.get("url") or hit.get("ref") or hit.get("title") or "").lower()
        if not key:
            continue
        if key not in merged:
            merged[key] = hit
            continue
        prev = merged[key]
        if _social_hit_rank(hit) > _social_hit_rank(prev):
            merged[key] = hit
    return sorted(merged.values(), key=_social_hit_rank, reverse=True)[:limit]


def _social_hit_rank(hit: ToolHit) -> float:
    status = str(hit.get("content_status") or "").lower()
    platform = str(hit.get("platform") or hit.get("source") or "").lower()
    content_len = len(str(hit.get("post_body") or hit.get("content") or hit.get("text") or ""))
    rank = float(hit.get("score") or 0.0)
    if status == "full_text":
        rank += 0.35
    elif status in {"snippet", "raw_content"}:
        rank += 0.18
    elif status in {"title_only", "search_only", "read_failed"}:
        rank -= 0.08
    if "小红书" in platform or "xiaohongshu" in platform:
        rank += 0.04
    if hit.get("fetch_method") == "tavily_ugc":
        rank += 0.03
    rank += min(content_len, 1200) / 10000.0
    return rank


async def _social_search_impl_v2(args: dict) -> List[ToolHit]:
    query = (args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 3)
    requested_sources = args.get("sources") or ["xiaohongshu"]
    if isinstance(requested_sources, str):
        requested_sources = [requested_sources]
    requested_sources = {str(s).strip().lower() for s in requested_sources if str(s).strip()}
    if "all" in requested_sources:
        requested_sources = set(SOCIAL_SOURCES.keys()) | {"ugc", "zhihu", "weibo"}
    top_k = max(1, min(top_k, 5))
    if not query:
        return [{"ref": "error", "text": "social_search.query is required"}]

    hits: List[ToolHit] = []
    xhs_full_text = False
    wants_xhs = not requested_sources or "xiaohongshu" in requested_sources
    wants_explicit_ugc = "ugc" in requested_sources
    wants_ugc_fallback = wants_xhs or bool({"ugc", "web", "wechat", "weibo", "zhihu"} & requested_sources)

    if wants_xhs:
        try:
            from urllib.parse import quote

            xhs_bin = _find_xhs_binary()
            if xhs_bin:
                code, raw, _stderr = await _run_xhs_command(
                    xhs_bin,
                    ["search", query],
                    timeout=TOOL_TIMEOUT_SECONDS + 5,
                )
                candidates = _extract_xhs_candidates(raw if code == 0 else "", query, top_k)
                for candidate in candidates:
                    title = candidate.get("title") or f"小红书搜索结果：{query[:60]}"
                    read_payload = await _read_xhs_post(xhs_bin, candidate.get("read_target", ""))
                    post_body = read_payload.get("post_body", "")
                    read_title = read_payload.get("title") or title
                    read_url = read_payload.get("url") or candidate.get("open_url", "")
                    open_url, open_url_type = _normalize_xhs_open_url(
                        read_url or candidate.get("raw_url", ""),
                        candidate.get("note_id", ""),
                        query,
                    )
                    if post_body:
                        xhs_full_text = True
                        content_status = "full_text"
                        fetch_method = "xhs_read"
                        content = post_body
                        score = 0.82
                        evidence_limit = "小红书正文属于用户经验帖或平台讨论，能说明常见说法与传播语境，不能单独证明医学或科学结论。"
                    else:
                        content_status = "read_failed" if candidate.get("read_target") else "title_only"
                        fetch_method = "xhs_search"
                        content = f"小红书搜索结果标题：{title[:180]}"
                        score = 0.55
                        evidence_limit = "仅获得小红书搜索标题，未抓取到原帖正文；不能据此判断作者完整观点。"
                    hits.append({
                        "ref": f"xhs:{fetch_method}:{abs(hash(read_title + open_url)) % 10**8}",
                        "title": read_title[:160],
                        "text": content[:500],
                        "content": content[:1200],
                        "post_body": post_body[:1800],
                        "source": "小红书",
                        "platform": "小红书",
                        "url": open_url,
                        "open_url": open_url,
                        "open_url_type": open_url_type,
                        "score": score,
                        "evidence_type": "social_opinion",
                        "content_available": bool(post_body),
                        "content_status": content_status,
                        "fetch_method": fetch_method,
                        "summary_note": content_status,
                        "evidence_limit": evidence_limit,
                    })
                    if len(hits) >= top_k:
                        break

            if not hits:
                search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(query)}"
                hits.append({
                    "ref": f"xhs:search:{abs(hash(query)) % 10**8}",
                    "title": f"小红书搜索：{query[:60]}",
                    "text": "仅检索到小红书搜索入口，未获取到可核验的原帖正文。",
                    "content": "仅检索到小红书搜索入口，未获取到可核验的原帖正文。",
                    "source": "小红书搜索结果",
                    "platform": "小红书",
                    "url": search_url,
                    "open_url": search_url,
                    "open_url_type": "search",
                    "score": 0.35,
                    "evidence_type": "social_opinion",
                    "content_available": False,
                    "content_status": "search_only",
                    "fetch_method": "xhs_search",
                    "summary_note": "search_only",
                    "evidence_limit": "只获得小红书搜索页入口，未抓取原帖正文。",
                })
        except Exception as e:
            logger.warning(f"[SocialTool/xhs] error: {type(e).__name__}")
            if not hits:
                from urllib.parse import quote

                search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(query)}"
                hits.append({
                    "ref": f"xhs:search:{abs(hash(query)) % 10**8}",
                    "title": f"小红书搜索：{query[:60]}",
                    "text": "小红书 CLI 调用失败，仅保留搜索入口；未获取到可核验的原帖正文。",
                    "content": "小红书 CLI 调用失败，仅保留搜索入口；未获取到可核验的原帖正文。",
                    "source": "小红书搜索结果",
                    "platform": "小红书",
                    "url": search_url,
                    "open_url": search_url,
                    "open_url_type": "search",
                    "score": 0.25,
                    "evidence_type": "social_opinion",
                    "content_available": False,
                    "content_status": "search_only",
                    "fetch_method": "xhs_search",
                    "summary_note": "search_only",
                    "evidence_limit": "小红书 CLI 调用失败，未抓取原帖正文。",
                })

    if wants_ugc_fallback and (wants_explicit_ugc or not xhs_full_text or len(hits) < top_k):
        remaining = min(top_k, 3) if wants_explicit_ugc else max(1, top_k - len(hits))
        hits.extend(await _tavily_ugc_search(query, remaining))

    return _dedupe_social_hits(hits, min(8, top_k + 3) if wants_explicit_ugc else top_k)


async def _social_search_impl(args: dict) -> List[ToolHit]:
    return await _social_search_impl_v2(args)

    """
    args = {
        "query": <string>,
        "sources": <list>,     # ["wechat", "xiaohongshu", "web"] 或 ["all"]
        "top_k": <int, default 3>,
    }
    通过 Agent-Reach 工具链检索中文社交平台内容。
    """
    query = (args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 3)
    requested_sources = args.get("sources") or ["xiaohongshu"]
    if isinstance(requested_sources, str):
        requested_sources = [requested_sources]
    requested_sources = {str(s).strip().lower() for s in requested_sources if str(s).strip()}
    if "all" in requested_sources:
        requested_sources = set(SOCIAL_SOURCES.keys())
    top_k = max(1, min(top_k, 5))
    if not query:
        return [{"ref": "error", "text": "social_search.query is required"}]

    hits: List[ToolHit] = []

    # 小红书搜索（通过 xhs-cli；不可用时返回真实搜索页，不伪造正文）
    if not requested_sources or "xiaohongshu" in requested_sources:
        try:
            import shutil
            from urllib.parse import quote

            xhs_bin = shutil.which("xhs")
            if xhs_bin:
                proc = await asyncio.create_subprocess_exec(
                    xhs_bin, "search", query,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
                stdout, _stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=TOOL_TIMEOUT_SECONDS + 5
                )
                raw = stdout.decode("utf-8", errors="replace")
                titles = _extract_xhs_field_values(raw, "display_title", "title", "note_title")
                note_ids = _extract_xhs_field_values(raw, "note_id", "noteId", "id")
                raw_urls = _extract_xhs_field_values(raw, "share_link", "note_url", "web_url", "xhs_url", "link", "url")
                for i, title in enumerate(t for t in titles if t):
                    raw_url = raw_urls[i] if i < len(raw_urls) else ""
                    note_id = note_ids[i] if i < len(note_ids) else ""
                    open_url, open_url_type = _normalize_xhs_open_url(raw_url, note_id, query)
                    has_direct_post = open_url_type == "post"
                    hits.append({
                        "ref": f"xhs:search:{abs(hash(title + open_url)) % 10**8}",
                        "title": title[:120],
                        "text": f"小红书搜索结果标题：{title[:180]}",
                        "source": "小红书",
                        "platform": "小红书",
                        "url": open_url,
                        "open_url": open_url,
                        "open_url_type": open_url_type,
                        "score": 0.6,
                        "evidence_type": "social_opinion",
                        "content_available": False,
                        "summary_note": "title_only" if has_direct_post else "search_only",
                    })
                    if len(hits) >= top_k:
                        break

            if not hits:
                search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(query)}"
                hits.append({
                    "ref": f"xhs:search:{abs(hash(query)) % 10**8}",
                    "title": f"小红书搜索：{query[:60]}",
                    "text": "仅检索到小红书搜索入口，未获取到可核验的原帖正文。",
                    "source": "小红书搜索结果",
                    "platform": "小红书",
                    "url": search_url,
                    "open_url": search_url,
                    "open_url_type": "search",
                    "score": 0.35,
                    "evidence_type": "social_opinion",
                    "content_available": False,
                    "summary_note": "search_only",
                })
        except Exception as e:
            logger.warning(f"[SocialTool/xhs] error: {e}")

    return hits[:top_k]


TOOL_IMPL: Dict[str, Callable[[dict], Awaitable[List[ToolHit]]]] = {
    "kg_query":   _kg_query_impl,
    "rag_search": _rag_search_impl,
    "web_search": _web_search_impl,
    "pubmed_search": _pubmed_search_impl,
    "social_search": _social_search_impl,
}


def _hash_args(tool: str, args: dict) -> str:
    s = tool + "|" + json.dumps(args, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(s.encode("utf-8")).hexdigest()


class ToolRegistry:
    """
    会话级工具注册表。每个 MADDx 会话 new 一个，持有自己的缓存。
    """

    def __init__(self, bb: Blackboard, enabled: Optional[List[str]] = None):
        self.bb = bb
        # 注意：enabled=[] 表示显式禁用所有工具（消融 B 组）；enabled=None 才是默认全开
        self.enabled = set(enabled) if enabled is not None else set(TOOL_IMPL.keys())
        self._cache: Dict[str, ToolResult] = {}      # hashed_args -> ToolResult
        self._total_calls = 0
        self._total_hits = 0

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def total_hits(self) -> int:
        return self._total_hits

    def schema_for_llm(self) -> str:
        """
        生成喂给 agent 的 Markdown 工具说明。被 agent_loop 拼进 system prompt。
        """
        if not self.enabled:
            return (
                "【工具不可用】本轮没有任何工具对你开放，禁止输出 action=\"tool_call\"。"
                "请直接基于症状和档案输出 action=\"finish\"。"
            )
        lines = ["你可用的工具如下（每次只能选一个工具调用，参数用 JSON 对象）："]
        if "kg_query" in self.enabled:
            lines.append(
                '- `kg_query`：医学知识图谱查询。args: {"mode": "disease_symptoms"|"symptom_diseases"'
                '|"disease_department"|"disease_drugs"|"drug_contraindications", "name": "<疾病名/症状名/药名>", "limit": 1~10}'
            )
        if "rag_search" in self.enabled:
            lines.append(
                '- `rag_search`：本地医学知识库向量检索（指南 / 说明书 / 教材片段）。'
                'args: {"query": "<自然语言查询>", "top_k": 1~10}'
            )
        if "web_search" in self.enabled:
            lines.append(
                '- `web_search`：权威医学站群搜索（CDC / NEJM / 丁香园等）。延迟高，仅在本地证据不足时调用。'
                'args: {"query": "<自然语言查询>", "top_k": 1~5}'
            )
        if "social_search" in self.enabled:
            lines.append(
                '- `social_search`：小红书 + UGC 舆情搜索。获取中文社区真实用户的健康讨论和亲身经验。'
                'args: {"query": "<自然语言查询>", "sources": ["xiaohongshu", "ugc"], "top_k": 1~5}'
            )
        if "pubmed_search" in self.enabled:
            lines.append(
                '- `pubmed_search`：PubMed 学术文献检索。获取原始研究论文的标题和摘要。'
                '适用于 NOVEL_TREND / EFFICACY 类需要最新科研证据的场景。args: {"query": "<检索词>", "top_k": 1~5}'
            )
        return "\n".join(lines)

    async def invoke(
        self,
        tool: str,
        args: dict,
        caller_agent: str,
        caller_round: int,
    ) -> ToolResult:
        """
        统一调用入口。自动留痕（tool_call + tool_result 两个 Blackboard entry）
        + 会话缓存 + SSE 事件推送。
        """
        if tool not in self.enabled:
            logger.warning(f"[Tool] 未启用的工具被调用: {tool} (caller={caller_agent})")
            return {
                "call_ref": -1, "tool": tool, "hits": [], "hit_count": 0, "cached": False,
            }

        # ---- 1. 写入 tool_call 留痕 ----
        call_entry: ToolCall = {
            "tool": tool,
            "args": args,
            "caller_agent": caller_agent,
            "caller_round": caller_round,
        }
        call_v = await self.bb.append("tool_call", call_entry, agent_id=caller_agent)

        # ---- 2. SSE 通知前端"谁在查什么" ----
        await sse_emit(
            "maddx_step",
            phase="tool_call",
            round=caller_round,
            agent=caller_agent,
            tool=tool,
            args=args,
            call_ref=call_v,
        )

        # ---- 3. 查缓存 ----
        key = _hash_args(tool, args)
        if key in self._cache:
            cached = dict(self._cache[key])
            cached["cached"] = True
            cached["call_ref"] = call_v
            await self.bb.append(
                "tool_result", cached, agent_id=caller_agent, parent_refs=[call_v]
            )
            await sse_emit(
                "maddx_step",
                phase="tool_result",
                round=caller_round,
                agent=caller_agent,
                tool=tool,
                call_ref=call_v,
                hit_count=cached["hit_count"],
                cached=True,
            )
            return cached

        # ---- 4. 实际执行 ----
        self._total_calls += 1
        impl = TOOL_IMPL.get(tool)
        if impl is None:
            hits: List[ToolHit] = []
        else:
            try:
                hits = await impl(args) or []
            except Exception as e:
                logger.error(f"[Tool] {tool} raised {e}")
                hits = []
        hit_count = len(hits)
        self._total_hits += hit_count

        result: ToolResult = {
            "call_ref": call_v,
            "tool": tool,
            "hits": hits,
            "hit_count": hit_count,
            "cached": False,
        }

        # ---- 5. 存缓存 + 写回黑板 ----
        self._cache[key] = result
        await self.bb.append(
            "tool_result", result, agent_id=caller_agent, parent_refs=[call_v]
        )

        await sse_emit(
            "maddx_step",
            phase="tool_result",
            round=caller_round,
            agent=caller_agent,
            tool=tool,
            call_ref=call_v,
            hit_count=hit_count,
            cached=False,
            preview=[
                (h.get("text") or f"{h.get('subject','')} {h.get('predicate','')} {h.get('object','')}").strip()[:80]
                for h in hits[:3]
            ],
        )
        return result
