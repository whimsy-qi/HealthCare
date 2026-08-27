"""
R2 — Claim-Type-Aware Evidence Weight Policy
=============================================
给定 claim_type，返回 (w_kg, w_rag, w_web) 三元权重；
并把权重换算成 per-source retrieval budget 与 per-agent tool budget。

这是 D9 的核心理论贡献之一：不同医疗谣言类型应有不同的证据信任优先级。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple, List, Literal, Optional

ClaimType = Literal[
    "CAUSAL",         # "X 导致 Y"
    "COMPOSITIONAL",  # "X 含 Y 所以有 Z 效果"
    "EFFICACY",       # "X 能治/能预防 Y"
    "DOSAGE",         # "吃多少 X"
    "INTERACTION",    # "X 与 Y 一起吃"
    "POPULATION",     # "孕妇/老人不能 X"
    "NOVEL_TREND",    # 新冠偏方/网红食疗
    "FOLKLORE",       # 老祖宗说 / 民俗
    "UNKNOWN",        # 分类器置信度低时兜底
]

ALL_CLAIM_TYPES: List[ClaimType] = [
    "CAUSAL", "COMPOSITIONAL", "EFFICACY", "DOSAGE",
    "INTERACTION", "POPULATION", "NOVEL_TREND", "FOLKLORE", "UNKNOWN",
]

Source = Literal["kg", "rag", "web", "social", "unknown"]

# ---------------------------------------------------------------------
# 权重食谱（论文 Table 2 会放这张表）
# ---------------------------------------------------------------------
#   (w_kg, w_rag, w_web)  每行三数之和 = 1.0
#   设计原则：
#     · KG 擅长结构化事实（成分、禁忌、药典剂量）→ COMPOSITIONAL/DOSAGE/INTERACTION 高
#     · RAG 擅长临床指南语义 → EFFICACY/POPULATION/CAUSAL 高
#     · Web 擅长时效性信息 → NOVEL_TREND 高
#     · FOLKLORE 需要跨源平衡，KG/RAG 通常查不到"捂汗治感冒"这种民间偏方
# ---------------------------------------------------------------------
CLAIM_TYPE_WEIGHTS: Dict[ClaimType, Tuple[float, float, float]] = {
    "CAUSAL":         (0.30, 0.50, 0.20),
    "COMPOSITIONAL":  (0.60, 0.30, 0.10),
    "EFFICACY":       (0.25, 0.55, 0.20),
    "DOSAGE":         (0.50, 0.45, 0.05),
    "INTERACTION":    (0.60, 0.35, 0.05),
    "POPULATION":     (0.35, 0.55, 0.10),
    "NOVEL_TREND":    (0.10, 0.30, 0.60),
    "FOLKLORE":       (0.20, 0.40, 0.40),
    "UNKNOWN":        (0.33, 0.34, 0.33),   # 均衡兜底（也是消融 baseline B 组使用的权重）
}

# 静态均衡权重（供消融 B 组对照使用）
STATIC_UNIFORM_WEIGHTS: Tuple[float, float, float] = (0.33, 0.34, 0.33)

# ---------------------------------------------------------------------
# Retrieval budget 换算
# ---------------------------------------------------------------------

TOTAL_RETRIEVAL_BUDGET = 10   # 每个 agent 单轮 top_k 的总预算
MIN_BUDGET_PER_SOURCE  = 1    # 即使权重很低，至少给 1（否则消融 B 无法公平对比）


@dataclass(frozen=True)
class WeightProfile:
    """一次谣言验证使用的权重快照，扔进 Blackboard 留痕用。"""
    claim_type: ClaimType
    w_kg: float
    w_rag: float
    w_web: float
    # per-source retrieval top_k
    budget_kg: int
    budget_rag: int
    budget_web: int
    # Social evidence is useful for spread/common-argument analysis, but it
    # must stay weaker than scientific or medical authority sources.
    w_social: float = 0.0
    source: str = "policy_table"  # 可扩展为 "learned" / "manual"

    def as_dict(self) -> dict:
        return {
            "claim_type": self.claim_type,
            "weights": {"kg": self.w_kg, "rag": self.w_rag, "web": self.w_web, "social": self.w_social},
            "budgets": {"kg": self.budget_kg, "rag": self.budget_rag, "web": self.budget_web},
            "source": self.source,
        }

    def weights_dict(self) -> Dict[Source, float]:
        return {"kg": self.w_kg, "rag": self.w_rag, "web": self.w_web, "social": self.w_social, "unknown": 0.0}


SOCIAL_WEIGHT_BY_CLAIM_TYPE: Dict[ClaimType, float] = {
    "NOVEL_TREND": 0.15,
    "FOLKLORE": 0.15,
    "EFFICACY": 0.08,
    "CAUSAL": 0.06,
    "COMPOSITIONAL": 0.05,
    "DOSAGE": 0.04,
    "INTERACTION": 0.04,
    "POPULATION": 0.04,
    "UNKNOWN": 0.08,
}


def _alloc_budget(weights: Tuple[float, float, float], total: int, floor: int) -> Tuple[int, int, int]:
    """把权重按比例分配成整数 budget，保证每源 >= floor，并使总和 = total。"""
    raw = [w * total for w in weights]
    alloc = [max(floor, round(r)) for r in raw]
    diff = total - sum(alloc)
    # 差额最大/最小权重项回调（保证总和精确 = total）
    idx_order = sorted(range(3), key=lambda i: weights[i], reverse=True)
    i = 0
    while diff != 0 and i < 6:  # 至多调整 6 次
        k = idx_order[i % 3]
        if diff > 0:
            alloc[k] += 1; diff -= 1
        elif diff < 0 and alloc[k] > floor:
            alloc[k] -= 1; diff += 1
        i += 1
    return tuple(alloc)  # type: ignore[return-value]


def resolve_weights(
    claim_type: ClaimType,
    total_budget: int = TOTAL_RETRIEVAL_BUDGET,
    floor: int = MIN_BUDGET_PER_SOURCE,
    uniform: bool = False,
) -> WeightProfile:
    """
    主入口：给定 claim_type，返回完整 WeightProfile。

    Args:
        claim_type: 8 类之一
        total_budget: 总 top_k 预算
        floor: 每源最少 top_k
        uniform: True 则强制走均衡权重（消融 B 组用）
    """
    if claim_type not in CLAIM_TYPE_WEIGHTS:
        claim_type = "UNKNOWN"

    w = STATIC_UNIFORM_WEIGHTS if uniform else CLAIM_TYPE_WEIGHTS[claim_type]
    social_w = 0.0 if uniform else SOCIAL_WEIGHT_BY_CLAIM_TYPE.get(claim_type, 0.08)
    base_scale = 1.0 - social_w
    w = tuple(round(x * base_scale, 4) for x in w)  # type: ignore[assignment]
    b_kg, b_rag, b_web = _alloc_budget(w, total_budget, floor)

    return WeightProfile(
        claim_type=claim_type,
        w_kg=w[0], w_rag=w[1], w_web=w[2],
        w_social=social_w,
        budget_kg=b_kg, budget_rag=b_rag, budget_web=b_web,
        source=("uniform" if uniform else "policy_table"),
    )


def resolve_composite_weights(
    primary: ClaimType,
    secondary: Optional[ClaimType] = None,
    primary_confidence: float = 1.0,
    secondary_confidence: float = 0.0,
    total_budget: int = TOTAL_RETRIEVAL_BUDGET,
    floor: int = MIN_BUDGET_PER_SOURCE,
    uniform: bool = False,
) -> WeightProfile:
    """Blend primary and secondary claim-type policies into one profile."""
    if uniform:
        return resolve_weights(primary, total_budget=total_budget, floor=floor, uniform=True)

    if primary not in CLAIM_TYPE_WEIGHTS:
        primary = "UNKNOWN"
    if secondary not in CLAIM_TYPE_WEIGHTS:
        secondary = None

    primary_weight = max(0.1, min(1.0, float(primary_confidence or 0.0)))
    secondary_weight = 0.0
    if secondary and secondary != primary:
        secondary_weight = max(0.0, min(0.7, float(secondary_confidence or 0.0)))

    total = primary_weight + secondary_weight
    if total <= 0:
        primary_weight, secondary_weight, total = 1.0, 0.0, 1.0

    p = tuple(x * (primary_weight / total) for x in CLAIM_TYPE_WEIGHTS[primary])
    if secondary and secondary != primary:
        s = tuple(x * (secondary_weight / total) for x in CLAIM_TYPE_WEIGHTS[secondary])
        base = tuple(p[i] + s[i] for i in range(3))
    else:
        base = CLAIM_TYPE_WEIGHTS[primary]

    social_primary = SOCIAL_WEIGHT_BY_CLAIM_TYPE.get(primary, 0.08)
    social_secondary = SOCIAL_WEIGHT_BY_CLAIM_TYPE.get(secondary, 0.0) if secondary else 0.0
    social_w = max(social_primary, social_secondary)
    if secondary in ("FOLKLORE", "NOVEL_TREND") or primary in ("FOLKLORE", "NOVEL_TREND"):
        social_w = max(social_w, 0.15)
    social_w = min(0.20, social_w)

    base_scale = 1.0 - social_w
    weights = tuple(round(x * base_scale, 4) for x in base)
    b_kg, b_rag, b_web = _alloc_budget(weights, total_budget, floor)

    label = primary if not secondary or secondary == primary else f"{primary}+{secondary}"
    return WeightProfile(
        claim_type=primary,
        w_kg=weights[0], w_rag=weights[1], w_web=weights[2],
        w_social=social_w,
        budget_kg=b_kg, budget_rag=b_rag, budget_web=b_web,
        source=f"composite_policy:{label}",
    )


def normalize_source_type(source_type: Any = "", evidence_type: Any = "") -> Source:
    """Map loose tool/LLM source labels into the four scoring buckets."""
    raw = str(source_type or "").strip().lower()
    ev = str(evidence_type or "").strip().lower()
    token = ev or raw

    if token in {"kg", "kg_query", "kg_structured", "knowledge_graph"} or raw in {"kg", "kg_query"}:
        return "kg"
    if token in {"rag", "rag_local", "local_rag", "pdf", "doc", "guideline"} or raw in {"rag", "rag_search"}:
        return "rag"
    if token in {"pubmed", "medical_authority", "clinical_guideline", "paper"}:
        return "rag"
    if token in {"web", "web_search", "scientific_web", "authority_web", "medical_web"} or raw in {"web", "web_search"}:
        return "web"
    if token in {"social", "social_search", "social_opinion", "xiaohongshu", "xhs", "wechat", "weibo", "bilibili", "zhihu"}:
        return "social"
    if raw in {"social", "xiaohongshu", "xhs", "wechat", "weibo", "bilibili", "zhihu"}:
        return "social"
    return "unknown"


def canonical_evidence_type(source_type: Any = "", evidence_type: Any = "") -> str:
    """Return a stable evidence_type while preserving known fine-grained labels."""
    ev = str(evidence_type or "").strip().lower()
    if ev in {
        "medical_authority", "scientific_web", "social_opinion",
        "kg_structured", "rag_local", "pubmed", "unknown",
    }:
        return ev
    bucket = normalize_source_type(source_type, evidence_type)
    return {
        "kg": "kg_structured",
        "rag": "rag_local",
        "web": "scientific_web",
        "social": "social_opinion",
        "unknown": "unknown",
    }[bucket]


_STRENGTH_MULTIPLIER = {
    "strong": 1.0,
    "moderate": 0.75,
    "medium": 0.75,
    "weak": 0.45,
}

_CONTENT_STATUS_MULTIPLIER = {
    "full_text": 1.0,
    "snippet": 0.82,
    "summary": 0.78,
    "title_only": 0.45,
    "search_only": 0.35,
    "read_failed": 0.30,
}

_DIRECTNESS_MULTIPLIER = {
    "direct": 1.0,
    "indirect": 0.70,
    "background": 0.45,
}

_SOURCE_BASE_QUALITY = {
    "kg": 0.72,
    "rag": 0.84,
    "web": 0.70,
    "social": 0.35,
    "unknown": 0.20,
}


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_evidence_quality(evidence: Dict[str, Any]) -> float:
    """
    Score one evidence item for adjudication.

    The score is deliberately conservative for social evidence: UGC can show
    spread and common arguments, but it cannot outweigh medical/scientific
    sources by itself.
    """
    if not isinstance(evidence, dict):
        return 0.0

    bucket = normalize_source_type(evidence.get("source_type"), evidence.get("evidence_type"))
    base = _SOURCE_BASE_QUALITY.get(bucket, 0.20)

    ev_type = canonical_evidence_type(evidence.get("source_type"), evidence.get("evidence_type"))
    if ev_type in {"medical_authority", "pubmed"}:
        base = min(1.0, base + 0.08)
    elif ev_type == "social_opinion":
        base = min(base, 0.35)

    strength = str(evidence.get("strength") or "moderate").strip().lower()
    content_status = str(evidence.get("content_status") or "snippet").strip().lower()
    directness = str(evidence.get("directness") or "direct").strip().lower()

    relevance = _float_or_default(evidence.get("relevance_score"), 0.70)
    relevance = max(0.10, min(1.0, relevance))

    if not (evidence.get("summary") or evidence.get("content") or evidence.get("snippet") or evidence.get("claim_aspect")):
        relevance = min(relevance, 0.35)

    score = (
        base
        * _STRENGTH_MULTIPLIER.get(strength, 0.65)
        * _CONTENT_STATUS_MULTIPLIER.get(content_status, 0.75)
        * _DIRECTNESS_MULTIPLIER.get(directness, 0.70)
        * relevance
    )
    return round(max(0.0, min(1.0, score)), 4)


def enrich_evidence_metadata(evidence: Dict[str, Any], stance: Optional[str] = None) -> Dict[str, Any]:
    """Normalize source metadata and attach scoring fields in-place."""
    if not isinstance(evidence, dict):
        return evidence
    bucket = normalize_source_type(evidence.get("source_type"), evidence.get("evidence_type"))
    evidence["source_type"] = bucket
    evidence["evidence_type"] = canonical_evidence_type(bucket, evidence.get("evidence_type"))
    evidence.setdefault("directness", "direct")
    evidence.setdefault("content_status", "snippet")
    if stance:
        evidence["stance"] = stance
    evidence["quality_score"] = score_evidence_quality(evidence)
    return evidence


def score_hits_per_source(evidence_list: List[dict]) -> Tuple[Dict[Source, float], Dict[str, Any]]:
    """Return quality-score sums by source and a compact quality summary."""
    buckets: Dict[Source, float] = {"kg": 0.0, "rag": 0.0, "web": 0.0, "social": 0.0, "unknown": 0.0}
    counts: Dict[Source, int] = {"kg": 0, "rag": 0, "web": 0, "social": 0, "unknown": 0}
    scores: List[float] = []

    for ev in evidence_list:
        if not isinstance(ev, dict):
            continue
        enrich_evidence_metadata(ev)
        src = normalize_source_type(ev.get("source_type"), ev.get("evidence_type"))
        score = float(ev.get("quality_score") or 0.0)
        buckets[src] = buckets.get(src, 0.0) + score
        counts[src] = counts.get(src, 0) + 1
        scores.append(score)

    total_score = round(sum(scores), 4)
    summary = {
        "counts": counts,
        "score_by_source": {k: round(v, 4) for k, v in buckets.items()},
        "total_quality_score": total_score,
        "average_quality_score": round(total_score / max(1, len(scores)), 4),
    }
    return buckets, summary


# ---------------------------------------------------------------------
# Weighted belief aggregation
# ---------------------------------------------------------------------
# 供 Judge 使用：把 Advocate / Skeptic 两方的证据按源分桶，算加权 belief 分。

def compute_weighted_belief(
    advocate_hits_per_source: Dict[Source, int],
    skeptic_hits_per_source: Dict[Source, int],
    weights: Dict[Source, float],
    epsilon: float = 1e-3,
) -> Tuple[float, Dict[Source, float], float]:
    """
    计算加权 belief 分数：
      net_i     = (a_i - s_i) / (a_i + s_i + ε)      ∈ [-1, +1]
      belief    = Σ w_i · net_i                      ∈ [-1, +1]
      dissent   = 1 - Σ w_i · overlap_i  （用双方命中差绝对值衡量共识度）

    Returns:
        (belief, per_source_net, dissent_score)
    """
    net_scores: Dict[Source, float] = {}
    belief = 0.0
    dissent_sum = 0.0

    for src in ("kg", "rag", "web", "social"):
        a = advocate_hits_per_source.get(src, 0)
        s = skeptic_hits_per_source.get(src, 0)
        total = a + s + epsilon
        net = (a - s) / total
        net_scores[src] = net
        w = weights.get(src, 0.0)
        belief += w * net
        # dissent 分量：双方命中同样多时（net≈0 但 a,s 都 > 0）dissent 大
        # 仅当双方都有命中时才计；没命中无话语权
        if a + s > 0:
            overlap = min(a, s) / max(a, s, 1)
            dissent_sum += w * overlap

    belief = max(-1.0, min(1.0, belief))
    dissent = max(0.0, min(1.0, dissent_sum))
    return belief, net_scores, dissent


# 判定阈值（论文实验中在 eval set 上扫 ROC 选定）
BELIEF_THRESHOLD_TRUE:  float = +0.35    # belief > +τ → "属实"
BELIEF_THRESHOLD_FALSE: float = -0.35    # belief < -τ → "谣言"
# |belief| ≤ τ 进一步看 dissent：低 dissent = "尚无定论" ; 高 dissent = "误导/有争议"


def classify_verdict(belief: float, dissent: float,
                     tau_hi: float = BELIEF_THRESHOLD_TRUE,
                     tau_lo: float = BELIEF_THRESHOLD_FALSE) -> str:
    """把 belief/dissent 映射到 4 类定性标签。"""
    if belief >= tau_hi:
        return "属实"
    if belief <= tau_lo:
        return "谣言"
    # 接近中立地带
    if dissent >= 0.5:
        return "误导"        # 双方都有证据，冲突强 → 片面误导
    return "尚无定论"          # 双方都没什么证据 → 不好判


import math


def evidence_sufficiency(total_hits: int, k: float = 3.0) -> float:
    """
    指数饱和型『证据充分度』，取代线性 coverage。

    设计理由：当 STRONG_CONSENSUS 早停时（例如只有 3-5 条决定性证据），
    线性 coverage = hits / (2*budget) 会被狠狠压低（可能到 0.1 以下），
    使 confidence 崩塌。而饱和式不会 —— 3 条就接近 0.63，6 条 >0.85。

    不同 hits 下的数值参考（k=3）：
        1 → 0.283      3 → 0.632      6 → 0.865      10 → 0.964
    """
    if total_hits <= 0:
        return 0.0
    return 1.0 - math.exp(-total_hits / max(k, 0.1))


def calibrated_confidence(
    belief: float,
    dissent: float,
    evidence_sufficiency_score: float,
) -> float:
    """
    置信度 = |belief| · (1 - dissent) · evidence_sufficiency

    · |belief| ∈ [0,1]        : 越偏离 0 越确定
    · (1 - dissent) ∈ [0,1]   : 共识越强越确定
    · evidence_sufficiency    : 证据充分度（推荐用 evidence_sufficiency() 计算，饱和式避免早停时 confidence 崩塌）
    """
    cov = max(0.0, min(1.0, evidence_sufficiency_score))
    return round(abs(belief) * (1.0 - dissent) * cov, 3)
