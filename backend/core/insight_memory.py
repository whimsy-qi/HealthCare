"""
🧠 Insight Memory — 见解知识库（双桶隐私分层版 v2）
==================================================

设计参考：
- 刘嘉等《基于多智能体系统的中文学术问答与应用》(昆明理工大学学报 2025.3)
  的"适应性学术见解学习系统"：评分 ≥ 8 触发反思 → 把【失败-成功经验对】
  存入【学术问答见解知识库】，下次相似问题直接复用。

医疗场景做了四处加固：
  1. 双极性存储：成功案例（quality 高）作为 few-shot 正例；
                  失败/弃答案例（hallucination_score 高 / verdict=尚无定论）
                  作为反例 anti-pattern，提醒下游 agent "这种情况要保守"。
  2. 风险加权质量分：hallucination_score + confidence 加权，
                      高风险域（rumor/medication）的 quality 计算更严格。
  3. 时间衰减 + 命中度排序：retrieval 时既看相似度也看 (hit_count / age)，
                              避免新问题永远命中过时的老旧 insight。
  4. 🔒【双桶隐私分层】（v2 新增）：
        - 通用知识型（不含个人健康信息）→ user_id=NULL 共享桶
        - 个性化型（含"我/我妈/我儿子" + 健康自述）→ user_id=该用户 私有桶
        - 检索时一次拉两批：当前用户的私有 ∪ 全库共享，再融合排序
        - 写入前自动脱敏（手机号/身份证/邮箱/座机）+ LLM 隐私分类

工程要点：
  - SQLite 单文件零运维（backend/data/insight_memory.sqlite）
  - dashscope text_embedding_v3（1024 维，与 KG/RAG 同源）
  - 同步 sqlite3 + asyncio.to_thread 兼容现有 async 主循环
  - LRU 缓存热查询的 embedding，避免频繁调云端
  - Top-K cosine 检索 + 阈值过滤 + 同 domain 隔离
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import struct
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional, Tuple

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

logger = logging.getLogger("InsightMemory")
INSIGHT_AUTO_HARVEST = os.getenv("INSIGHT_AUTO_HARVEST", "false").lower() in {"1", "true", "yes", "on"}


# =============================================================================
# 1. 数据契约
# =============================================================================

Polarity = Literal["SUCCESS", "FAILURE"]            # 正例 / 反例
Domain   = Literal["rumor", "general", "medication", "symptom", "report"]


@dataclass
class Insight:
    """单条见解记录。落库 / 检索时使用。"""
    id: int = 0
    domain: Domain = "general"
    query: str = ""
    polarity: Polarity = "SUCCESS"
    agent_path: str = ""                # e.g. "triage→general" 或 "rumor:CTAEW"
    final_answer: str = ""              # 完整 markdown 答案（可选）
    answer_summary: str = ""            # 答案的 1-2 句精炼摘要（用于 few-shot 注入）
    evidence_count: int = 0             # 命中证据条数
    hallucination_score: float = 0.0    # [0,1]，0=无幻觉
    confidence: float = 0.0             # 模型 / 终审给出的可信度
    quality_score: float = 0.0          # 综合质量分（生成时计算）
    tags: List[str] = field(default_factory=list)
    hit_count: int = 0                  # 被检索复用的次数
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    # 🧠 自我反思
    failure_analysis: str = ""          # 失败时 LLM 生成的根因分析
    suggested_fix: str = ""             # 建议的改进策略

    # 🔒 v2 新增：隐私分层
    user_id: Optional[int] = None       # NULL = 共享桶；有值 = 该用户私有
    is_personal: bool = False           # 是否含个人健康信息

    # 检索阶段才填充：
    similarity: float = 0.0
    final_score: float = 0.0            # similarity × recency × quality

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# 2. SQLite 存储层
# =============================================================================

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "insight_memory.sqlite",
)
EMBED_DIM = 1024  # text_embedding_v3

# 表 DDL（建表）
_SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS insights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain          TEXT NOT NULL,
    query           TEXT NOT NULL,
    polarity        TEXT NOT NULL CHECK(polarity IN ('SUCCESS', 'FAILURE')),
    agent_path      TEXT,
    final_answer    TEXT,
    answer_summary  TEXT,
    evidence_count  INTEGER DEFAULT 0,
    hallucination_score REAL DEFAULT 0.0,
    confidence      REAL DEFAULT 0.0,
    quality_score   REAL DEFAULT 0.0,
    embedding       BLOB NOT NULL,
    tags            TEXT DEFAULT '[]',
    hit_count       INTEGER DEFAULT 0,
    created_at      REAL DEFAULT (strftime('%s','now')),
    last_used_at    REAL DEFAULT (strftime('%s','now')),
    user_id         INTEGER,                       -- NULL = 共享桶；有值 = 私有桶
    is_personal     INTEGER DEFAULT 0,             -- 0/1
    fingerprint     TEXT NOT NULL UNIQUE
);
"""

# 索引（必须在 ALTER TABLE 后执行，否则旧表迁移时找不到 user_id 列）
_SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_insights_domain ON insights(domain);
CREATE INDEX IF NOT EXISTS idx_insights_polarity ON insights(polarity);
CREATE INDEX IF NOT EXISTS idx_insights_quality ON insights(quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_insights_user ON insights(user_id);
CREATE INDEX IF NOT EXISTS idx_insights_user_domain ON insights(user_id, domain);
"""

# 旧版 schema 平滑升级（生产环境实例已有数据时不会丢失）
def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(insights)")
    cols = {row[1] for row in cur.fetchall()}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE insights ADD COLUMN user_id INTEGER")
        logger.info("[Insight/Schema] 已添加 user_id 字段（v1 → v2）")
    if "is_personal" not in cols:
        conn.execute("ALTER TABLE insights ADD COLUMN is_personal INTEGER DEFAULT 0")
        logger.info("[Insight/Schema] 已添加 is_personal 字段（v1 → v2）")
    if "failure_analysis" not in cols:
        conn.execute("ALTER TABLE insights ADD COLUMN failure_analysis TEXT DEFAULT ''")
        conn.execute("ALTER TABLE insights ADD COLUMN suggested_fix TEXT DEFAULT ''")
        logger.info("[Insight/Schema] 已添加 failure_analysis / suggested_fix 字段（v2 → v3）")


def _encode_vec(vec: List[float]) -> bytes:
    """1024 维 float vec → 紧凑 4096-byte binary blob（float32）。"""
    if len(vec) != EMBED_DIM:
        raise ValueError(f"vec dim {len(vec)} != EMBED_DIM {EMBED_DIM}")
    return struct.pack(f"{EMBED_DIM}f", *vec)


def _decode_vec(blob: bytes) -> List[float]:
    return list(struct.unpack(f"{EMBED_DIM}f", blob))


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db_sync() -> None:
    with _connect() as conn:
        # 1. 先建表（v1 旧库时不会重建）
        conn.executescript(_SCHEMA_TABLE)
        # 2. 再做列迁移（v1 → v2 加 user_id / is_personal）
        _migrate_v1_to_v2(conn)
        # 3. 最后建索引（确保所有列都存在）
        conn.executescript(_SCHEMA_INDEXES)


# 模块加载即建库（小成本、避免首次调用竞争）
_init_db_sync()


# =============================================================================
# 3. 嵌入服务（dashscope text_embedding_v3）
# =============================================================================

# 进程内 LRU 缓存，避免同一 query 反复请求 API
_embed_cache: Dict[str, List[float]] = {}
_embed_cache_lock = asyncio.Lock()
_embed_cache_max = 256


def _digest(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def _embed_sync(text: str) -> Optional[List[float]]:
    """阻塞调 dashscope；调用方负责放 to_thread。"""
    try:
        import dashscope
        if not dashscope.api_key:
            dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
        resp = dashscope.TextEmbedding.call(
            model=dashscope.TextEmbedding.Models.text_embedding_v3,
            input=[text[:2048]],
        )
        if getattr(resp, "status_code", 200) != 200:
            logger.warning(f"[Embed] dashscope code={resp.status_code} msg={resp.message}")
            return None
        return resp.output["embeddings"][0]["embedding"]
    except Exception as e:
        logger.warning(f"[Embed] 异常: {e}")
        return None


async def embed(text: str) -> Optional[List[float]]:
    """异步嵌入（带 LRU 缓存）。"""
    if not text or not text.strip():
        return None
    key = _digest(text)
    async with _embed_cache_lock:
        cached = _embed_cache.get(key)
    if cached is not None:
        return cached

    vec = await asyncio.to_thread(_embed_sync, text)
    if vec is None:
        return None

    async with _embed_cache_lock:
        if len(_embed_cache) >= _embed_cache_max:
            _embed_cache.pop(next(iter(_embed_cache)), None)
        _embed_cache[key] = vec
    return vec


# =============================================================================
# 4. 质量分公式
# =============================================================================

# domain 风险加权：医疗高风险域对幻觉容忍度更低
_DOMAIN_STRICT = {
    "rumor":      1.20,  # 谣言判定容易"自信地答错"，加严
    "medication": 1.20,  # 用药永远 HIGH
    "general":    1.00,
    "symptom":    0.95,
    "report":     1.05,
}


# =============================================================================
# 4'. 🔒 PII 脱敏 + 隐私分类器（v2 双桶分层关键组件）
# =============================================================================

# 正则脱敏：手机号 / 身份证 / 邮箱 / 座机
_PII_RULES: List[Tuple[Any, str]] = []  # 延迟编译，避免顶层 import 时 re 未就绪

def _ensure_pii_rules() -> None:
    global _PII_RULES
    if _PII_RULES:
        return
    import re as _re
    # 顺序很重要：长模式先于短模式，否则身份证会被手机号正则吞掉
    _PII_RULES = [
        # 身份证（18 位，必须放最前）
        (_re.compile(r"\b\d{17}[\dXx]\b"),               "[身份证]"),
        # 邮箱
        (_re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),       "[邮箱]"),
        # 座机
        (_re.compile(r"\b\d{3,4}-\d{7,8}\b"),            "[座机]"),
        # 手机（11 位）
        (_re.compile(r"\b1[3-9]\d{9}\b"),                "[手机号]"),
        # 中文姓 + 称呼（简版兜底，不覆盖所有姓氏）
        (_re.compile(r"(?<![a-zA-Z])[一-龥]{1}(先生|女士|同志|主任|医生)"), r"\1"),
    ]


def redact_pii(text: str) -> str:
    """正则级 PII 脱敏（写入 query/answer_summary 之前调一次）。"""
    if not text:
        return text
    _ensure_pii_rules()
    out = text
    for pat, repl in _PII_RULES:
        out = pat.sub(repl, out)
    return out


# LLM 隐私分类器：判断 query 是否含【提问者本人 / 家人】的健康信息
_PRIVACY_SYSTEM = """你是隐私敏感度分类器。判断给定问题是否包含【提问者本人或家人的个人健康信息】。

【personal=true 的标志】：第一/第二人称健康自述。包含"我/我的/我爸/我妈/我儿子/孩子" + 任何健康/疾病/服药/检查/年龄信息。
  示例：
    - "我有糖尿病能吃 X 吗"
    - "我妈对头孢过敏，能吃 Y 吗"
    - "我 5 岁的孩子发烧到 39 度怎么办"
    - "我每天吃二甲双胍，能喝酒吗"
    - "我体检尿酸 520 怎么办"

【personal=false 的标志】：客观医学问题、谣言核查、通用科普。**不出现自我代入或家庭成员**。
  示例：
    - "微波炉加热食物致癌吗"
    - "感冒药可以一次吃几片"
    - "二甲双胍空腹吃吗"
    - "吸烟和肺癌的关系"
    - "高血压患者饮食注意什么"

【输出严格 JSON】：{"is_personal": true|false, "reason": "<不超过 30 字>"}"""


async def classify_privacy(query: str) -> bool:
    """
    LLM 隐私分类。失败时保守降级为 True（默认进私有桶，宁可错放也不泄露）。
    """
    query = (query or "").strip()
    if not query:
        return False
    try:
        from core.llm_client import shared_client as _client, FAST_MODEL
        resp = await _client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {"role": "system", "content": _PRIVACY_SYSTEM},
                {"role": "user",   "content": f"问题：{query}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=80,
        )
        data = json.loads(resp.choices[0].message.content)
        return bool(data.get("is_personal", False))
    except Exception as e:
        logger.warning(f"[Privacy] 分类失败 → 保守判定为 personal: {e}")
        return True


# =============================================================================
# 4''. 质量分公式（保持不变）
# =============================================================================

def compute_quality_score(
    confidence: float,
    hallucination_score: float,
    evidence_count: int,
    domain: str = "general",
    polarity: Polarity = "SUCCESS",
) -> float:
    """
    生成单条 insight 的综合质量分（0-1）。

    设计：
      - SUCCESS：confidence 高 + halluc 低 + 有证据 → 高分
      - FAILURE：用 1 - 上式（让"反例越糟糕，越值得记忆"）
      - domain_strict 让 rumor/medication 的高分门槛更高
    """
    confidence = max(0.0, min(1.0, confidence))
    hallucination_score = max(0.0, min(1.0, hallucination_score))
    strict = _DOMAIN_STRICT.get(domain, 1.0)

    # 基础分：confidence 占 50%, (1 - halluc) 占 35%, evidence 充足度占 15%
    evidence_bonus = min(1.0, evidence_count / 5.0)
    base = 0.50 * confidence + 0.35 * (1.0 - hallucination_score) + 0.15 * evidence_bonus

    # 严格度惩罚（仅作用于成功例：rumor/medication 想拿高分得更扎实）
    if polarity == "SUCCESS":
        base = base ** strict
    else:
        # 失败例的 quality 表示"该案例多有警示价值"：
        # 高幻觉 + 当时还自信 = 最有警示意义（要重点记忆）
        base = 0.55 * hallucination_score + 0.45 * confidence

    return round(max(0.0, min(1.0, base)), 4)


# =============================================================================
# 5. 写入 / 检索
# =============================================================================

async def add_insight(
    *,
    domain: Domain,
    query: str,
    user_id: Optional[int] = None,
    is_personal: Optional[bool] = None,
    final_answer: str = "",
    answer_summary: str = "",
    agent_path: str = "",
    evidence_count: int = 0,
    hallucination_score: float = 0.0,
    confidence: float = 0.0,
    polarity: Polarity = "SUCCESS",
    tags: Optional[List[str]] = None,
    embedding: Optional[List[float]] = None,
    auto_classify_privacy: bool = True,
    failure_analysis: str = "",
    suggested_fix: str = "",
) -> Optional[int]:
    """
    向见解知识库追加一条记录（v2 双桶版）。

    隐私策略：
      - is_personal=None + auto_classify_privacy=True → LLM 自动分类
      - is_personal=True 但传了 user_id=None → 保护性升级为 user_id 必填，否则直接拒收
      - is_personal=False → 强制 user_id=NULL（共享桶）
      - is_personal=True  → 强制 user_id=该用户（私有桶）

    指纹策略：
      - 共享桶（user_id=NULL）：fingerprint = f"{domain}|{query}|{polarity}|shared"
      - 私有桶（user_id=X） ：fingerprint = f"{domain}|{query}|{polarity}|user:{X}"
      → 同样的问题，A 用户的私有版 + B 用户的私有版 + 共享版可以共存
    """
    query = (query or "").strip()
    if not query:
        return None

    # === 1. PII 脱敏（写入前清洗） ===
    query_clean = redact_pii(query)
    answer_summary_clean = redact_pii(answer_summary)
    final_answer_clean = redact_pii(final_answer)

    # === 2. 隐私分类（决定走哪个桶） ===
    if is_personal is None and auto_classify_privacy:
        is_personal = await classify_privacy(query_clean)
    if is_personal is None:
        is_personal = False

    # 个性化但没给 user_id → 保守拒收（不泄露）
    if is_personal and user_id is None:
        logger.warning(
            f"[Insight] 检测到个人健康信息但未提供 user_id，安全拒收: '{query_clean[:50]}'"
        )
        return None

    # 非个性化强制走共享桶（即使调用方塞了 user_id 也忽略）
    final_user_id: Optional[int] = user_id if is_personal else None

    # === 3. 嵌入 ===
    if embedding is None:
        embedding = await embed(query_clean)
    if embedding is None:
        logger.warning("[Insight] 嵌入失败，跳过该案例落库。")
        return None

    # === 4. 质量分 ===
    quality = compute_quality_score(
        confidence=confidence,
        hallucination_score=hallucination_score,
        evidence_count=evidence_count,
        domain=domain,
        polarity=polarity,
    )

    # === 5. 指纹包含 user_id 桶位，使私有 vs 共享可以并存 ===
    bucket = f"user:{final_user_id}" if final_user_id is not None else "shared"
    fingerprint = _digest(f"{domain}|{query_clean}|{polarity}|{bucket}")
    blob = _encode_vec(embedding)
    tags_str = json.dumps(tags or [], ensure_ascii=False)

    def _insert():
        with _connect() as conn:
            cur = conn.execute("SELECT id, quality_score FROM insights WHERE fingerprint = ?", (fingerprint,))
            row = cur.fetchone()
            if row:
                # 仅当新质量分更高时覆盖
                if quality > row["quality_score"]:
                    conn.execute(
                        """UPDATE insights SET
                            agent_path=?, final_answer=?, answer_summary=?,
                            evidence_count=?, hallucination_score=?, confidence=?,
                            quality_score=?, embedding=?, tags=?,
                            last_used_at=strftime('%s','now')
                          WHERE id=?""",
                        (agent_path, final_answer_clean[:8000], answer_summary_clean[:600],
                         evidence_count, hallucination_score, confidence,
                         quality, blob, tags_str, row["id"])
                    )
                    return row["id"]
                else:
                    return row["id"]
            cur = conn.execute(
                """INSERT INTO insights
                   (domain, query, polarity, agent_path, final_answer, answer_summary,
                    evidence_count, hallucination_score, confidence, quality_score,
                    embedding, tags, user_id, is_personal, fingerprint,
                    failure_analysis, suggested_fix)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (domain, query_clean, polarity, agent_path,
                 final_answer_clean[:8000], answer_summary_clean[:600],
                 evidence_count, hallucination_score, confidence, quality,
                 blob, tags_str,
                 final_user_id, 1 if is_personal else 0, fingerprint,
                 failure_analysis, suggested_fix)
            )
            return cur.lastrowid

    try:
        new_id = await asyncio.to_thread(_insert)
        bucket_label = f"private(user={final_user_id})" if final_user_id is not None else "shared"
        logger.info(
            f"🧠 [Insight] 入库 id={new_id} domain={domain} polarity={polarity} "
            f"bucket={bucket_label} quality={quality} q='{query_clean[:40]}'"
        )
        return new_id
    except Exception as e:
        logger.error(f"[Insight] 写入异常: {e}")
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


_RECENCY_HALFLIFE_DAYS = 30.0  # 新近权重的半衰期


def _recency_factor(created_at: float) -> float:
    age_days = max(0.0, (time.time() - created_at) / 86400.0)
    # 半衰期衰减：当 age = HALFLIFE 时权重 = 0.5；age=0 → 1.0
    return 0.5 ** (age_days / _RECENCY_HALFLIFE_DAYS)


async def retrieve_insights(
    *,
    query: str,
    user_id: Optional[int] = None,
    domain: Optional[Domain] = None,
    polarity: Optional[Polarity] = None,
    top_k: int = 3,
    min_similarity: float = 0.78,
    embedding: Optional[List[float]] = None,
    include_shared: bool = True,
) -> List[Insight]:
    """
    检索 Top-K 相似见解（v2 双桶版）。综合 similarity + recency + quality 排序。

    隐私策略：
      - user_id=None: 仅检索共享桶（user_id IS NULL）
      - user_id=X + include_shared=True：检索 (user_id=X) ∪ (user_id IS NULL)
      - user_id=X + include_shared=False：仅检索该用户私有桶
      → 默认 include_shared=True：每个用户既能用别人的通用知识，也能看自己的历史

    Args:
        query:           当前问题
        user_id:         当前用户 ID（用于拉取私有桶）
        domain:          仅限本 domain（None = 全库）
        polarity:        仅取正例 / 反例（None = 全部）
        top_k:           返回条数
        min_similarity:  阈值；过低不返回（避免乱蹭）
        include_shared:  是否包含共享桶
    """
    if not query or not query.strip():
        return []
    if embedding is None:
        embedding = await embed(query)
    if embedding is None:
        return []

    def _query():
        clauses: List[str] = []
        params: List[Any] = []

        # 桶过滤：私有 + 共享 / 仅私有 / 仅共享
        if user_id is not None and include_shared:
            clauses.append("(user_id = ? OR user_id IS NULL)")
            params.append(user_id)
        elif user_id is not None and not include_shared:
            clauses.append("user_id = ?")
            params.append(user_id)
        else:
            clauses.append("user_id IS NULL")

        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if polarity:
            clauses.append("polarity = ?")
            params.append(polarity)

        sql = (
            "SELECT * FROM insights WHERE "
            + " AND ".join(clauses)
            + " ORDER BY quality_score DESC LIMIT 200"
        )
        with _connect() as conn:
            return conn.execute(sql, params).fetchall()

    rows = await asyncio.to_thread(_query)
    if not rows:
        return []

    candidates: List[Insight] = []
    for r in rows:
        try:
            vec = _decode_vec(r["embedding"])
        except Exception:
            continue
        sim = _cosine(embedding, vec)
        if sim < min_similarity:
            continue
        ins = Insight(
            id=r["id"], domain=r["domain"], query=r["query"], polarity=r["polarity"],
            agent_path=r["agent_path"] or "", final_answer=r["final_answer"] or "",
            answer_summary=r["answer_summary"] or "",
            evidence_count=r["evidence_count"] or 0,
            hallucination_score=r["hallucination_score"] or 0.0,
            confidence=r["confidence"] or 0.0,
            quality_score=r["quality_score"] or 0.0,
            tags=json.loads(r["tags"] or "[]"),
            hit_count=r["hit_count"] or 0,
            created_at=r["created_at"] or time.time(),
            last_used_at=r["last_used_at"] or time.time(),
            user_id=r["user_id"] if "user_id" in r.keys() else None,
            is_personal=bool(r["is_personal"]) if "is_personal" in r.keys() else False,
            similarity=sim,
            failure_analysis=(r["failure_analysis"] or "") if "failure_analysis" in r.keys() else "",
            suggested_fix=(r["suggested_fix"] or "") if "suggested_fix" in r.keys() else "",
        )
        rec = _recency_factor(ins.created_at)
        ins.final_score = round(sim * 0.65 + ins.quality_score * 0.25 + rec * 0.10, 4)
        candidates.append(ins)

    candidates.sort(key=lambda x: x.final_score, reverse=True)
    top = candidates[:top_k]

    if top:
        ids = [i.id for i in top]
        def _bump():
            with _connect() as conn:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE insights SET hit_count = hit_count + 1, "
                    f"last_used_at = strftime('%s','now') WHERE id IN ({placeholders})",
                    ids,
                )
        try:
            await asyncio.to_thread(_bump)
        except Exception as e:
            logger.warning(f"[Insight] hit_count 更新失败: {e}")

    return top


# =============================================================================
# 6. Few-shot 注入辅助
# =============================================================================

def render_insights_as_fewshot(insights: List[Insight], max_chars: int = 1200) -> str:
    """
    把检索到的 insight 列表渲染成可注入 system prompt 的 few-shot 文本。
    分为【正例】（值得参考）+【反例】（避免重蹈覆辙）两块。
    """
    if not insights:
        return ""

    pos = [i for i in insights if i.polarity == "SUCCESS"]
    neg = [i for i in insights if i.polarity == "FAILURE"]
    parts: List[str] = ["【相似历史案例参考】"]

    if pos:
        parts.append("\n— ✅ 可参考的成功案例：")
        for i, ins in enumerate(pos, 1):
            sim_pct = int(ins.similarity * 100)
            sum_or_ans = ins.answer_summary or ins.final_answer[:200]
            parts.append(
                f"  {i}. [相似度 {sim_pct}% · 质量 {ins.quality_score:.2f}] "
                f"问题：{ins.query[:70]}\n"
                f"     当时回答要点：{sum_or_ans[:200]}"
            )

    if neg:
        parts.append("\n— ⚠️ 反例与失败反思（避免犯同样错误）：")
        for i, ins in enumerate(neg, 1):
            sim_pct = int(ins.similarity * 100)
            sum_or_ans = ins.answer_summary or ins.final_answer[:200]
            parts.append(
                f"  {i}. [相似度 {sim_pct}% · 当时幻觉分 {ins.hallucination_score:.2f}] "
                f"问题：{ins.query[:70]}\n"
                f"     失误教训：{sum_or_ans[:200]}"
            )
            if ins.failure_analysis:
                parts.append(f"     🔍 根因分析：{ins.failure_analysis[:150]}")
            if ins.suggested_fix:
                parts.append(f"     💡 改进建议：{ins.suggested_fix[:150]}")

    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[…见解上下文已截断]"
    return text


# =============================================================================
# 7. 维护 / 调试工具
# =============================================================================

# =============================================================================
# 7'. 收割钩子：把 hallucination 报告自动转写为 insight
# =============================================================================

# 兜底总结模板（halluc 报告里没 summary 字段时用）
def _derive_summary(final_answer: str, max_chars: int = 240) -> str:
    """从 markdown 答案里抽 1-2 句作为 summary（粗暴但够用）。"""
    if not final_answer:
        return ""
    # 优先取第一段非空、非标题的文字
    lines = [
        ln.strip() for ln in final_answer.splitlines()
        if ln.strip() and not ln.strip().startswith(("#", ">", "-", "*", "1.", "2.", "```"))
    ]
    if not lines:
        return final_answer[:max_chars]
    text = " ".join(lines[:2])
    return text[:max_chars]


# =============================================================================
# 7b. 自我反思生成器
# =============================================================================

REFLECTION_PROMPT = """你是多智能体医疗系统的质量分析师。以下 AI 回答被 HallucinationAgent 标记为问题回答。

【问题信息】
用户查询: {query}
AI 回答摘要: {answer_summary}
处理链路: {agent_path}
幻觉得分: {hallucination_score:.2f}
检测判定: {action}
证据数量: {evidence_count}

【请分析】
1. failure_root_cause: 这次回答的主要问题是什么？（1句话）
2. suggested_fix: 下次遇到类似问题应该怎么做？（1句话）
3. collab_recommendation: 当前使用的协作模式合适吗？如果应该换，推荐什么？

【输出严格 JSON】
{{"failure_root_cause": "...", "suggested_fix": "...", "collab_recommendation": "..."}}"""


async def generate_reflection(
    query: str,
    answer_summary: str,
    agent_path: str,
    hallucination_score: float,
    action: str,
    evidence_count: int = 0,
) -> tuple:
    """调 LLM 对失败案例做反思分析。返回 (failure_analysis, suggested_fix)。"""
    prompt = REFLECTION_PROMPT.format(
        query=query[:300], answer_summary=answer_summary[:300],
        agent_path=agent_path, hallucination_score=hallucination_score,
        action=action, evidence_count=evidence_count,
    )
    try:
        from core.llm_client import shared_client as _client, FAST_MODEL
        resp = await _client.chat.completions.create(
            model=FAST_MODEL, temperature=0.1, max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return (
            data.get("failure_root_cause", ""),
            data.get("suggested_fix", ""),
        )
    except Exception as e:
        logger.warning(f"[Reflection] LLM 调用失败，回退到模板反思: {e}")
        return (
            f"HallucinationAgent 检测到回答存在问题（action={action}, score={hallucination_score:.2f}），但自动反思生成失败",
            f"建议人工审核此 case 并考虑调整 {agent_path} 的协作策略",
        )


async def harvest_from_hallucination_report(
    *,
    query: str,
    domain: Domain,
    user_id: Optional[int],
    final_answer: str = "",
    halluc_report: Optional[Dict[str, Any]] = None,
    evidence_count: int = 0,
    agent_path: str = "",
    tags: Optional[List[str]] = None,
) -> Optional[int]:
    """
    在 hallucination_guard 完成后调一次：把检测员的 action / score 转化为 insight。

    映射规则：
        - action == PASS         → polarity = SUCCESS（高质量正例）
        - action == WARN          → polarity = SUCCESS（中等质量正例）
        - action == REGENERATE    → polarity = FAILURE（值得记忆的负例）
        - action == ABSTAIN       → polarity = FAILURE（强警示反例）
        - timeout / 异常          → 不入库（避免污染数据）

    建议在【非阻塞 fire-and-forget】场景调用：
        asyncio.create_task(harvest_from_hallucination_report(...))
    """
    if not query or not query.strip():
        return None
    if not INSIGHT_AUTO_HARVEST:
        logger.info(f"[Harvest/{domain}] 自动经验入库已关闭，等待 QA Review 审核")
        return None
    halluc_report = halluc_report or {}

    action = (halluc_report.get("action") or "PASS").upper()
    if halluc_report.get("timeout") or halluc_report.get("error"):
        # 检测器异常时不入库（避免错误信息污染未来检索）
        logger.info(f"[Harvest/{domain}] 检测器异常/超时，跳过入库")
        return None

    polarity: Polarity = "SUCCESS" if action in ("PASS", "WARN") else "FAILURE"
    halluc_score = float(halluc_report.get("hallucination_score", 0.0))
    confidence = float(halluc_report.get("confidence", 1.0 - halluc_score))

    summary = halluc_report.get("summary") or _derive_summary(final_answer)

    # 🧠 失败反思（非阻塞，失败不阻断主流程）
    failure_analysis = ""
    suggested_fix = ""
    if polarity == "FAILURE":
        try:
            failure_analysis, suggested_fix = await generate_reflection(
                query=query, answer_summary=summary, agent_path=agent_path,
                hallucination_score=halluc_score, action=action,
                evidence_count=evidence_count,
            )
        except Exception as e:
            logger.warning(f"[Harvest/{domain}] 反思生成异常（不阻断）: {e}")

    return await add_insight(
        domain=domain,
        query=query,
        user_id=user_id,
        is_personal=None,
        agent_path=agent_path,
        final_answer=final_answer,
        answer_summary=summary,
        evidence_count=evidence_count,
        hallucination_score=halluc_score,
        confidence=confidence,
        polarity=polarity,
        tags=tags or [],
        failure_analysis=failure_analysis,
        suggested_fix=suggested_fix,
    )


async def stats() -> Dict[str, Any]:
    def _q():
        with _connect() as conn:
            tot = conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0]
            by_dom = conn.execute(
                "SELECT domain, COUNT(*) c FROM insights GROUP BY domain"
            ).fetchall()
            by_pol = conn.execute(
                "SELECT polarity, COUNT(*) c FROM insights GROUP BY polarity"
            ).fetchall()
            top5 = conn.execute(
                "SELECT id, domain, query, polarity, quality_score, hit_count "
                "FROM insights ORDER BY hit_count DESC, quality_score DESC LIMIT 5"
            ).fetchall()
            return tot, by_dom, by_pol, top5
    tot, by_dom, by_pol, top5 = await asyncio.to_thread(_q)
    return {
        "total": tot,
        "by_domain":   {r["domain"]: r["c"] for r in by_dom},
        "by_polarity": {r["polarity"]: r["c"] for r in by_pol},
        "hottest": [dict(r) for r in top5],
    }


async def purge_low_quality(min_quality: float = 0.20) -> int:
    """清理低质量（既不是好正例，也没什么警示价值的反例）记录。"""
    def _del():
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM insights WHERE quality_score < ? AND hit_count = 0",
                (min_quality,)
            )
            return cur.rowcount
    n = await asyncio.to_thread(_del)
    logger.info(f"[Insight] 清理 {n} 条低质量记录")
    return n
