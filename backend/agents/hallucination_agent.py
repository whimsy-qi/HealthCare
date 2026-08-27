# backend/agents/hallucination_agent.py
"""
🛡️ Hallucination Checker Agent
================================
独立的"幻觉检测员"智能体 —— 在 rumor / general / medication 三条核心链路的出口
对模型生成的医疗回答做 **证据对齐式幻觉检测**，并产出可执行的兜底动作建议。

设计参考：
- 王路桥等《基于大语言模型的多智能体协作代码评审人推荐》(软件学报 2025.6)
  的"幻觉检测员"角色 —— 但本实现针对医疗场景做了三处加固：

  1. **claim 级原子分解**：把回答拆成可单独验证的事实声明，避免"整段话糊在一起"
     导致部分错误被均匀稀释。
  2. **证据对齐 NLI 判定**：每条 claim 在【SUPPORTED / PARTIAL / UNSUPPORTED /
     CONTRADICTED】四档分类，并要求模型输出 unsupported_span（具体不被支持的
     原文片段），便于上游做精准重写或高亮。
  3. **风险加权聚合**：诊断/用药类 claim 的权重远高于"多喝水"这类通用建议；
     一条高风险幻觉 ≈ 多条低风险幻觉，匹配医疗领域"对错误零容忍"的特性。

工程考量：
- 使用 shared_client 单例、复用 HTTP 连接池
- SHA-256(answer+evidence) → asyncio-safe LRU cache（512 entries、TTL 6h）
- 并发信号量限制（默认 4），避免突发请求把 LLM 配额打爆
- 整套调用 < 1 个 LLM token 单价，因为只用 deepseek-chat 单次结构化 JSON 调用
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional, Tuple

from core.llm_client import shared_client as client, REASONING_MODEL

logger = logging.getLogger("HallucinationAgent")


# =============================================================================
# 1. 数据契约
# =============================================================================

# 单条 claim 的对齐结论
ClaimVerdict = Literal["SUPPORTED", "PARTIAL", "UNSUPPORTED", "CONTRADICTED"]

# 整体兜底动作（按严重程度由轻到重）
RemediationAction = Literal["PASS", "WARN", "REGENERATE", "ABSTAIN"]

# 风险层级（继承 R10 的命名，便于跨模块互通）
RiskTier = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass
class ClaimAudit:
    """单条原子声明的对齐审计结果"""
    claim: str                              # 原子声明文本
    risk: RiskTier                          # 该 claim 的医疗风险层级
    verdict: ClaimVerdict                   # 对齐结论
    unsupported_span: str = ""              # 若 UNSUPPORTED/CONTRADICTED，指出原文片段
    rationale: str = ""                     # 法官对该判定的简短理由（< 60 字）
    matched_source_idx: List[int] = field(default_factory=list)  # 命中的证据下标

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HallucinationReport:
    """整篇回答的整体幻觉审计报告"""
    hallucination_score: float              # [0, 1]，越高越可疑
    confidence: float                       # 1 - hallucination_score（保留独立字段方便前端）
    action: RemediationAction               # 推荐兜底动作
    summary: str                            # 一句话总结，可直接展示
    claims: List[ClaimAudit] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    cache_hit: bool = False                 # 命中缓存则为 True，便于审计

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["claims"] = [c.as_dict() if hasattr(c, "as_dict") else c for c in self.claims]
        return d


# =============================================================================
# 2. 缓存与并发控制
# =============================================================================

_CACHE_MAX = 512
_CACHE_TTL_SEC = 6 * 60 * 60  # 6h

_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_cache_lock = asyncio.Lock()
_inflight: Dict[str, asyncio.Future] = {}        # 同 key 多并发请求合流
_sem = asyncio.Semaphore(4)                       # 最多 4 个 LLM 校验并发


def _digest(answer: str, evidence_blob: str, risk_tier: str, constraints_blob: str = "") -> str:
    h = hashlib.sha256()
    h.update(answer.strip().encode("utf-8"))
    h.update(b"||")
    h.update(evidence_blob.encode("utf-8"))
    h.update(b"||")
    h.update(constraints_blob.encode("utf-8"))
    h.update(b"||")
    h.update(risk_tier.encode("utf-8"))
    return h.hexdigest()


async def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    async with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        ts, val = item
        if time.time() - ts > _CACHE_TTL_SEC:
            _cache.pop(key, None)
            return None
        return val


async def _cache_set(key: str, val: Dict[str, Any]) -> None:
    async with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            # 简易 LRU：弹出最旧
            oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
            _cache.pop(oldest, None)
        _cache[key] = (time.time(), val)


# =============================================================================
# 3. 证据归一化
# =============================================================================

def _normalize_evidence(evidence: Any) -> Tuple[str, List[Dict[str, str]]]:
    """
    把上游五花八门的证据格式（list of dict / str / None）统一成两份：
      - 给 LLM 看的纯文本拼接
      - 给前端追溯用的 [{idx, title, snippet}] 列表
    """
    if not evidence:
        return "（无证据）", []

    if isinstance(evidence, str):
        return evidence[:4000], [{"idx": 0, "title": "上游证据", "snippet": evidence[:300]}]

    if isinstance(evidence, dict):
        evidence = [evidence]

    text_chunks: List[str] = []
    cards: List[Dict[str, str]] = []
    for i, item in enumerate(evidence[:8]):  # 最多 8 条，控成本
        if isinstance(item, str):
            snip = item[:600]
            text_chunks.append(f"[E{i}] {snip}")
            cards.append({"idx": i, "title": f"证据 {i}", "snippet": snip[:200]})
            continue
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or item.get("source") or item.get("name") or f"证据 {i}").strip()
        body = (
            item.get("content")
            or item.get("text")
            or item.get("snippet")
            or item.get("summary")
            or ""
        )
        body = str(body)[:600]
        if not body:
            continue
        text_chunks.append(f"[E{i}] 《{title}》：{body}")
        cards.append({"idx": i, "title": title, "snippet": body[:200]})

    return ("\n".join(text_chunks) if text_chunks else "（无证据）"), cards


def _normalize_constraints(constraints: Any) -> str:
    """Normalize KG/safety constraints. They can block unsafe claims, but cannot support citations."""
    if not constraints:
        return ""
    if isinstance(constraints, str):
        return constraints[:2500]
    if isinstance(constraints, dict):
        constraints = [constraints]
    chunks: List[str] = []
    if isinstance(constraints, list):
        for i, item in enumerate(constraints[:8]):
            if isinstance(item, str):
                body = item[:500]
            elif isinstance(item, dict):
                title = item.get("title") or item.get("label") or item.get("source") or f"constraint {i}"
                body = item.get("content") or item.get("text") or item.get("snippet") or item.get("summary") or ""
                body = f"{title}: {str(body)[:500]}"
            else:
                continue
            if body:
                chunks.append(f"[C{i}] {body}")
    return "\n".join(chunks)[:2500]


# =============================================================================
# 4. 风险加权
# =============================================================================

# 每个 verdict × risk 的权重矩阵
# 思路：
#  - SUPPORTED 不扣分
#  - PARTIAL 轻微扣分
#  - UNSUPPORTED 中等扣分
#  - CONTRADICTED 重扣（与证据相反 = 真·幻觉）
#  - 风险越高扣分越重（HIGH 的 UNSUPPORTED ≈ LOW 的 CONTRADICTED）
_VERDICT_WEIGHT = {
    "SUPPORTED":     {"LOW": 0.00, "MEDIUM": 0.00, "HIGH": 0.00},
    "PARTIAL":       {"LOW": 0.05, "MEDIUM": 0.10, "HIGH": 0.20},
    "UNSUPPORTED":   {"LOW": 0.15, "MEDIUM": 0.30, "HIGH": 0.55},
    "CONTRADICTED":  {"LOW": 0.35, "MEDIUM": 0.60, "HIGH": 0.90},
}


def _aggregate_score(claims: List[ClaimAudit]) -> float:
    """
    将 per-claim 扣分聚合成 [0, 1] 的整体幻觉分数。
    使用 1 - ∏(1 - w_i)：任意一条高风险幻觉就足以把整体分推到接近 1，
    比简单平均更能反映"医疗系统对最严重错误零容忍"的语义。
    """
    if not claims:
        return 0.0
    keep = 1.0
    for c in claims:
        w = _VERDICT_WEIGHT.get(c.verdict, {}).get(c.risk, 0.0)
        keep *= max(0.0, 1.0 - w)
    return round(1.0 - keep, 4)


def _decide_action(score: float, claims: List[ClaimAudit], domain_risk: RiskTier) -> RemediationAction:
    """
    根据整体分数 + 是否存在 HIGH/CONTRADICTED 决定兜底动作。
    domain_risk 是【整个回答】所属业务的风险层（rumor 高风险声明 = HIGH）。
    """
    has_high_contradict = any(c.verdict == "CONTRADICTED" and c.risk == "HIGH" for c in claims)
    has_any_contradict = any(c.verdict == "CONTRADICTED" for c in claims)

    # 一条 HIGH+CONTRADICTED 直接弃答 —— 医疗领域的红线
    if has_high_contradict:
        return "ABSTAIN"

    # HIGH 业务域 + 分数 > 0.5 → 弃答
    if domain_risk == "HIGH" and score >= 0.5:
        return "ABSTAIN"

    # 通用阈值
    if score >= 0.55:
        return "ABSTAIN"
    if score >= 0.30 or has_any_contradict:
        return "REGENERATE"
    if score >= 0.12:
        return "WARN"
    return "PASS"


# =============================================================================
# 5. 检测主流程（LLM 单次结构化校验）
# =============================================================================

# Prompt 中文版：要求把回答拆成 atomic claims，并对每条做对齐打分
_CHECKER_SYSTEM = """你是一名极其严苛的【医学事实核查官】。你的任务是审查一份 AI 医疗助手生成的回答（answer），
判断其中**每一条事实声明**是否被提供的【证据】（evidence）所支持。

【你必须遵守的核查纪律】
1. 仅依据【证据】判定。不要援引你自己的常识 —— 如果证据未提及，就标 UNSUPPORTED。
2. 把回答拆成 3-8 条原子声明（atomic claims）。每条必须可单独被支持/反驳。
3. 客套话、免责声明、问候语 → 不算事实声明，不要拆。
4. 数值、剂量、机制、禁忌 → 这些是 HIGH 风险声明；生活习惯建议是 MEDIUM；通用安慰话是 LOW。
5. 如果声明与证据矛盾（不是"未提及"，而是"明确相反"），必须标 CONTRADICTED。

【单条 claim 的判定档位】
- SUPPORTED      : 证据明确支持（关键词 + 语义都对得上）
- PARTIAL        : 证据部分支持，但有细节出入或泛化
- UNSUPPORTED    : 证据未提及（不一定错，但不能确认）
- CONTRADICTED   : 证据明确否定该声明（真·幻觉）

【输出严格 JSON，结构如下】
{
  "claims": [
    {
      "claim": "<原子声明文本，复述自回答>",
      "risk": "LOW | MEDIUM | HIGH",
      "verdict": "SUPPORTED | PARTIAL | UNSUPPORTED | CONTRADICTED",
      "unsupported_span": "<如果 UNSUPPORTED/CONTRADICTED，指出回答原文中该问题片段；否则空串>",
      "rationale": "<不超过 50 字的判定理由>",
      "matched_source_idx": [<命中的证据下标 E0/E1/... 中的 0/1/...，可空>]
    }
  ],
  "summary": "<一句话总结整体核查印象，不超过 60 字>"
}

⚠️ 仅输出 JSON，不要 Markdown、不要前缀解释。"""

_CHECKER_SYSTEM += """

【约束证据规则】
1. 【约束证据】通常来自知识图谱、患者档案或安全红线，只能用于判断回答是否违反禁忌、相互作用、剂量越权等安全约束。
2. 约束证据不能用于把医学事实标为 SUPPORTED。事实支持只能来自【可用证据】。
3. 如果回答与约束证据冲突，应标为 CONTRADICTED；如果回答试图仅凭约束证据下医学结论，应标为 UNSUPPORTED 或 PARTIAL。
"""


async def _llm_check(answer: str, evidence_text: str, constraints_text: str = "") -> Dict[str, Any]:
    """单次 LLM 调用：拆 claims 并对齐打分。"""
    user_msg = (
        f"【AI 医疗助手生成的回答】\n{answer.strip()[:3500]}\n\n"
        f"【可用证据】\n{evidence_text[:3500]}\n\n"
        f"【约束证据】\n{constraints_text[:2500] if constraints_text else '（无约束证据）'}"
    )

    async with _sem:
        resp = await client.chat.completions.create(
            model=REASONING_MODEL,
            messages=[
                {"role": "system", "content": _CHECKER_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,           # 校验任务一律 0 温度
            max_tokens=1200,
        )
    raw = resp.choices[0].message.content
    return json.loads(raw)


def _coerce_claim(d: Any) -> Optional[ClaimAudit]:
    if not isinstance(d, dict):
        return None
    claim = (d.get("claim") or "").strip()
    if not claim:
        return None
    verdict = (d.get("verdict") or "").upper().strip()
    if verdict not in ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "CONTRADICTED"):
        verdict = "UNSUPPORTED"
    risk = (d.get("risk") or "MEDIUM").upper().strip()
    if risk not in ("LOW", "MEDIUM", "HIGH"):
        risk = "MEDIUM"
    matched = d.get("matched_source_idx") or []
    if not isinstance(matched, list):
        matched = []
    matched = [int(x) for x in matched if isinstance(x, (int, float, str)) and str(x).isdigit()]
    return ClaimAudit(
        claim=claim[:300],
        risk=risk,                                # type: ignore[arg-type]
        verdict=verdict,                          # type: ignore[arg-type]
        unsupported_span=(d.get("unsupported_span") or "")[:300],
        rationale=(d.get("rationale") or "")[:200],
        matched_source_idx=matched[:5],
    )


# =============================================================================
# 6. 公开入口
# =============================================================================

async def check_answer(
    answer: str,
    evidence: Any = None,
    constraints: Any = None,
    domain: str = "general",
    domain_risk: RiskTier = "MEDIUM",
    timeout_sec: float = 24.0,
) -> HallucinationReport:
    """
    主入口：对一段医疗回答做幻觉检测。

    Args:
        answer: AI agent 生成的最终回答（Markdown 或纯文本）。
        evidence: 用于核验的证据，支持：
                  - List[Dict] (每条含 title/content)
                  - str (拼接好的纯文本)
                  - None (将给出 WARN，因无法核验)
        domain: 业务名（"rumor"/"general"/"medication"/...），仅用于日志。
        domain_risk: 当前业务整体风险层。rumor 中 HIGH 风险声明传 "HIGH"，
                     用药审查传 "HIGH"，通用咨询传 "MEDIUM"。
        timeout_sec: 单次 LLM 校验超时上限。超时一律返回 WARN 兜底。

    Returns:
        HallucinationReport（包含整体分数、动作建议、per-claim 审计）。
    """
    if not answer or not answer.strip():
        return HallucinationReport(
            hallucination_score=0.0, confidence=1.0,
            action="PASS", summary="空回答，跳过核查。", stats={"empty": 1},
        )

    # 证据归一化
    evidence_text, evidence_cards = _normalize_evidence(evidence)
    constraints_text = _normalize_constraints(constraints)
    no_evidence = evidence_text == "（无证据）"

    # 没有证据时直接降级到 WARN —— 既不能 PASS 也不该 ABSTAIN（还是有部分常识有用）
    if no_evidence:
        logger.info(f"🛡️ [Halluc/{domain}] 无证据可对齐 → 降级为 WARN")
        return HallucinationReport(
            hallucination_score=0.25, confidence=0.75,
            action="WARN", summary="未提供可对齐的证据，无法做事实核查，仅依据模型常识作答。",
            stats={"no_evidence": 1, "constraints_present": 1 if constraints_text else 0},
        )

    # 缓存命中
    key = _digest(answer, evidence_text, domain_risk, constraints_text)
    cached = await _cache_get(key)
    if cached:
        rep = HallucinationReport(**{**cached, "claims": [ClaimAudit(**c) for c in cached["claims"]]})
        rep.cache_hit = True
        logger.info(f"🛡️ [Halluc/{domain}] cache hit → action={rep.action} score={rep.hallucination_score}")
        return rep

  # in-flight 合流：同一 key 并发只调一次 LLM
    async with _cache_lock:
        fut = _inflight.get(key)
        if fut is None:
            fut = asyncio.get_event_loop().create_future()
            _inflight[key] = fut
            owner = True
        else:
            owner = False

    if not owner:
        try:
            cached2 = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout_sec)
            if cached2:
                rep = HallucinationReport(**{**cached2, "claims": [ClaimAudit(**c) for c in cached2["claims"]]})
                rep.cache_hit = True
                return rep
        except asyncio.TimeoutError:
            pass

    try:
        raw = await asyncio.wait_for(_llm_check(answer, evidence_text, constraints_text), timeout=timeout_sec)
        claims_raw = raw.get("claims") or []
        claims: List[ClaimAudit] = []
        for d in claims_raw[:12]:
            c = _coerce_claim(d)
            if c:
                claims.append(c)

        score = _aggregate_score(claims)
        action = _decide_action(score, claims, domain_risk)
        confidence = round(1.0 - score, 4)
        stats = {
            "n_claims": len(claims),
            "n_supported":     sum(1 for c in claims if c.verdict == "SUPPORTED"),
            "n_partial":       sum(1 for c in claims if c.verdict == "PARTIAL"),
            "n_unsupported":   sum(1 for c in claims if c.verdict == "UNSUPPORTED"),
            "n_contradicted":  sum(1 for c in claims if c.verdict == "CONTRADICTED"),
            "n_high_risk":     sum(1 for c in claims if c.risk == "HIGH"),
        }

        report = HallucinationReport(
            hallucination_score=score, confidence=confidence,
            action=action,
            summary=(raw.get("summary") or "").strip()[:200] or "已完成证据对齐核查。",
            claims=claims,
            stats=stats,
        )
        await _cache_set(key, report.as_dict())

        # 通知合流者
        async with _cache_lock:
            f = _inflight.pop(key, None)
        if f and not f.done():
            f.set_result(report.as_dict())

        logger.info(
            f"🛡️ [Halluc/{domain}] action={action} score={score} "
            f"contra={stats['n_contradicted']} unsup={stats['n_unsupported']} "
            f"claims={stats['n_claims']}"
        )
        return report

    except asyncio.TimeoutError:
        logger.warning(f"⏱️ [Halluc/{domain}] LLM 校验超时 → 降级 WARN")
        async with _cache_lock:
            f = _inflight.pop(key, None)
        if f and not f.done():
            f.set_exception(asyncio.TimeoutError())
        return HallucinationReport(
            hallucination_score=0.20, confidence=0.80,
            action="WARN", summary="幻觉检测超时，已放行但建议人工复核。",
            stats={"timeout": 1},
        )
    except Exception as e:
        logger.error(f"❌ [Halluc/{domain}] 异常: {e}")
        async with _cache_lock:
            f = _inflight.pop(key, None)
        if f and not f.done():
            f.set_exception(e)
        return HallucinationReport(
            hallucination_score=0.15, confidence=0.85,
            action="WARN", summary=f"幻觉检测系统波动（{type(e).__name__}），已放行。",
            stats={"error": 1},
        )


# =============================================================================
# 7. 兜底动作模板（供 graph_engine / agent 使用）
# =============================================================================

ABSTAIN_TEMPLATE = """### 🛡️ 系统稳健性提示

> 本次提问涉及**高风险医疗判断**，AI 在交叉核验后发现回答中存在**未被证据充分支持**的关键事实，
> 出于医疗安全考虑，系统已**主动放弃本次回答**。

请考虑以下做法：
- 📞 **联系正规医院或拨打 120**（如属急症）
- 👨‍⚕️ 携带相关检查报告**面诊专科医生**
- 🔁 您可以**补充更多上下文信息**后重新提问，AI 将再次核查

> *⚠️ 主动弃答机制：本系统在每次回答后均会启动独立的"幻觉检测员"智能体做证据对齐校验，
> 当判定回答不可信时，系统不会强行作答，这是医疗类 AI 的安全红线。*
"""


def render_warn_banner(report: HallucinationReport) -> str:
    """生成可挂在回答顶部的 WARN 横幅 Markdown。"""
    n_unsup = report.stats.get("n_unsupported", 0)
    n_contra = report.stats.get("n_contradicted", 0)
    suffix = ""
    if n_contra > 0:
        suffix = f"，其中 {n_contra} 条与证据存在出入"
    elif n_unsup > 0:
        suffix = f"，其中 {n_unsup} 条未在权威证据中找到直接支持"
    return (
        f"> 🟡 **可信度提示**：AI 已对本回答的 {report.stats.get('n_claims', 0)} 条事实声明做证据交叉核验"
        f"{suffix}。建议关键决策（用药、诊断）以专业医生意见为准。\n\n"
    )


def _claim_to_dict(claim: Any) -> Dict[str, Any]:
    if hasattr(claim, "as_dict"):
        return claim.as_dict()
    if isinstance(claim, dict):
        return claim
    return {}


def _problem_claims_for_rewrite(report: HallucinationReport) -> List[Dict[str, Any]]:
    claims: List[Dict[str, Any]] = []
    for claim in report.claims or []:
        c = _claim_to_dict(claim)
        if c.get("verdict") not in ("UNSUPPORTED", "CONTRADICTED", "PARTIAL"):
            continue
        claims.append({
            "claim": c.get("claim", ""),
            "verdict": c.get("verdict", ""),
            "risk": c.get("risk", ""),
            "unsupported_span": c.get("unsupported_span", ""),
            "rationale": c.get("rationale", ""),
        })
    return claims


async def _regenerate_conservative_answer(
    answer: str,
    report: HallucinationReport,
    evidence: Any,
    domain: str,
    constraints: Any = None,
) -> str:
    """
    One-pass conservative rewrite. It removes or weakens unsupported claims only;
    it does not introduce new medical facts beyond the supplied evidence.
    """
    problem_claims = _problem_claims_for_rewrite(report)
    if not problem_claims:
        return render_warn_banner(report) + answer

    evidence_text, _ = _normalize_evidence(evidence)
    constraints_text = _normalize_constraints(constraints)
    system_prompt = (
        "你是医疗回答的安全改写员。请只根据给定证据和问题声明改写原回答："
        "删除与证据矛盾的内容，弱化未被证据直接支持的断言，保留已支持的安全建议。"
        "不要加入新事实、不要暴露内部审计 JSON，直接输出给患者看的 Markdown。"
    )
    user_payload = {
        "domain": domain,
        "original_answer": answer[:3500],
        "problem_claims": problem_claims[:8],
        "available_evidence": evidence_text[:3500],
        "safety_constraints": constraints_text[:2500],
    }
    try:
        async with _sem:
            resp = await client.chat.completions.create(
                model=REASONING_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                temperature=0.0,
                max_tokens=1600,
            )
        rewritten = (resp.choices[0].message.content or "").strip()
        if rewritten:
            return rewritten
    except Exception as e:
        logger.warning(f"[Halluc/{domain}] conservative rewrite failed: {type(e).__name__}: {e}")
    return render_warn_banner(report) + answer


# =============================================================================
# 8. 通用后处理（供 general / medication 节点复用）
# =============================================================================

async def guard_answer(
    answer: str,
    evidence: Any,
    domain: str,
    domain_risk: RiskTier,
    audit_logs: Optional[List[str]] = None,
    timeout_sec: float = 20.0,
    constraints: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    通用守门员：对一段 markdown 回答 + 证据列表做幻觉检测，并按 action 重写回答。

    返回：
        (rewritten_markdown, report_dict_for_trace)

    设计要点：
      - 检测异常或超时不阻断主流程，降级为 WARN 横幅 + 通过
      - audit_logs 若传入则会注入审计行
      - 触发 SSE "hallucination_check" 事件供前端实时显示状态
      - 调用方拿到 report_dict 后挂到 trace_data["hallucination_check"] 即可
    """
    if not answer or not answer.strip():
        return answer, {}

    if isinstance(evidence, list):
        kg_constraints = [
            item for item in evidence
            if isinstance(item, dict) and (item.get("type") == "kg" or item.get("evidence_role") == "constraint")
        ]
        if kg_constraints:
            evidence = [
                item for item in evidence
                if not (isinstance(item, dict) and (item.get("type") == "kg" or item.get("evidence_role") == "constraint"))
            ]
            if constraints:
                constraints = (constraints if isinstance(constraints, list) else [constraints]) + kg_constraints
            else:
                constraints = kg_constraints

    # SSE：start（异步代码，emit 在 ContextVar 缺失时自动 no-op）
    try:
        from core.sse_emitter import emit as _sse_emit
        ev_count = (
            len(evidence) if isinstance(evidence, list)
            else (1 if evidence else 0)
        )
        await _sse_emit(
            "hallucination_check", phase="start",
            domain=domain, domain_risk=domain_risk, evidence_count=ev_count,
            message=f"🛡️ 启动幻觉检测员（domain={domain}, risk={domain_risk}, 证据 {ev_count} 条）…",
        )
    except Exception:
        _sse_emit = None  # type: ignore[assignment]

    try:
        report: HallucinationReport = await asyncio.wait_for(
            check_answer(
                answer=answer,
                evidence=evidence,
                constraints=constraints,
                domain=domain,
                domain_risk=domain_risk,
            ),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[{domain}/Halluc] 检测超时 → 静默放行原文")
        if audit_logs is not None:
            audit_logs.append(f"[Halluc/{domain}] 检测超时，静默放行。")
        try:
            from core.sse_emitter import emit as _sse_emit2
            await _sse_emit2("hallucination_check", phase="timeout",
                             domain=domain, action="WARN",
                             message="幻觉检测超时，已放行。")
        except Exception:
            pass
        return answer, {
            "action": "WARN",
            "timeout": True,
            "degraded": True,
            "safety_check_degraded": True,
            "summary": "Hallucination checker timed out; answer was not fully evidence-validated.",
        }
    except Exception as e:
        logger.error(f"[{domain}/Halluc] 异常: {e}")
        if audit_logs is not None:
            audit_logs.append(f"[Halluc/{domain}] 异常: {type(e).__name__}: {e}")
        return answer, {
            "action": "WARN",
            "error": str(e)[:120],
            "degraded": True,
            "safety_check_degraded": True,
            "summary": "Hallucination checker failed; answer was not fully evidence-validated.",
        }

    rep_dict = report.as_dict()
    if constraints:
        rep_dict["constraints_present"] = True
    if audit_logs is not None:
        audit_logs.append(
            f"[Halluc/{domain}] action={report.action} score={report.hallucination_score} "
            f"claims={report.stats.get('n_claims', 0)} "
            f"contra={report.stats.get('n_contradicted', 0)} "
            f"unsup={report.stats.get('n_unsupported', 0)}"
        )

    # SSE：done
    try:
        from core.sse_emitter import emit as _sse_emit3
        await _sse_emit3(
            "hallucination_check", phase="done",
            domain=domain, domain_risk=domain_risk,
            action=report.action,
            score=report.hallucination_score,
            confidence=report.confidence,
            n_claims=report.stats.get("n_claims", 0),
            n_contradicted=report.stats.get("n_contradicted", 0),
            n_unsupported=report.stats.get("n_unsupported", 0),
            summary=report.summary,
            message=f"🛡️ 幻觉检测员判定：{report.action}（可信度 {int(report.confidence * 100)}%）",
        )
    except Exception:
        pass

    if report.action == "ABSTAIN":
        if audit_logs is not None:
            audit_logs.append(f"[Halluc/{domain}] ABSTAIN applied: unsafe answer suppressed")
        return ABSTAIN_TEMPLATE, rep_dict

    if report.action == "WARN":
        if audit_logs is not None:
            audit_logs.append(f"[Halluc/{domain}] WARN applied: risk banner prepended")
        return render_warn_banner(report) + answer, rep_dict

    if report.action == "REGENERATE":
        rewritten = await _regenerate_conservative_answer(answer, report, evidence, domain, constraints)
        if audit_logs is not None:
            audit_logs.append(f"[Halluc/{domain}] REGENERATE applied: conservative rewrite returned")
        return rewritten, rep_dict

    return answer, rep_dict


# =============================================================================
# 8. 调试入口
# =============================================================================

if __name__ == "__main__":  # pragma: no cover
    import asyncio as _aio

    async def _demo():
        rep = await check_answer(
            answer=(
                "### 🛡️ 核查结论\n"
                "头孢与酒精会引起双硫仑反应，可能在 24 小时内危及生命。"
                "建议停药 7 天后再饮酒。同时建议每天饮用 8 杯白开水帮助代谢。"
            ),
            evidence=[
                {"title": "新版抗生素临床指南",
                 "content": "头孢类与酒精同服会诱发双硫仑样反应，表现为面部潮红、心悸、呼吸困难，严重时危及生命。停药后建议至少间隔 7 天再饮酒。"},
                {"title": "通用安全用药提示",
                 "content": "服药期间注意休息和饮食清淡。"},
            ],
            domain="rumor",
            domain_risk="HIGH",
        )
        print(json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))

    _aio.run(_demo())
