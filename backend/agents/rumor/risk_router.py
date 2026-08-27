"""
R10 — Query-Aware Risk Routing
================================
D9 的最终形态：根据 claim_type + 分类置信度对谣言命题分级，
低风险走 Fast-Path（单 LLM，1-2s）直接回答，高风险走 CTAEW
全流程（类型感知权重 + 对抗辩论 + 加权裁决，80s）。

设计依据（来自 ablation 数据 25 例 × 4 条件）：
    - Single-LLM (A) 在已知谣言上 loose acc = 100%，速度快
    - CTAEW (C)     优势在可审计证据链 + 拒绝过度自信，速度慢
    - 两者不是替代关系，而是**风险分层互补**

风险分级原则（"错误后果严重度"而非"准确率差异"）：
    HIGH   : INTERACTION / DOSAGE / POPULATION / NOVEL_TREND / UNKNOWN
             → 错误 → 患者直接伤害 或 LLM 知识盲区 → 必须走 CTAEW
    MEDIUM : EFFICACY
             → 错误 → 延误治疗 → 走 CTAEW（保守策略）
    LOW    : CAUSAL / COMPOSITIONAL
             → LLM 预训练充分覆盖 → 走 Fast-Path 换速度

额外提升条件（风险升级为 HIGH）：
    - 分类置信度 < 0.6 （分类不可信 → 保守走深度核查）
    - 命题长度 > 80 字符（复杂命题往往有多跳推理 → 保守）
"""
import json
import logging
from dataclasses import dataclass
from typing import Literal, Optional, Dict, Any

from core.llm_client import shared_client as client, FAST_MODEL
from .claim_classifier import ClassifyResult
from .weight_policy import ClaimType

logger = logging.getLogger("Rumor.RiskRouter")

RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
RouteDecision = Literal["LIGHT_CHECK", "FAST_PATH", "FULL_CTAEW"]


# ---------------------------------------------------------------------
# 风险分级表
# ---------------------------------------------------------------------

CLAIM_TYPE_BASE_RISK: Dict[ClaimType, RiskLevel] = {
    "INTERACTION":   "HIGH",    # 药物互作 → 中毒风险
    "DOSAGE":        "HIGH",    # 剂量错判 → 过量/疗效不足
    "POPULATION":    "HIGH",    # 孕妇/糖尿病禁忌 → 直接伤害
    "NOVEL_TREND":   "HIGH",    # LLM 训练数据可能过期
    "UNKNOWN":       "HIGH",    # 分类失败 → 保守兜底
    "EFFICACY":      "MEDIUM",  # 错误疗效 → 延误治疗
    "CAUSAL":        "LOW",     # 因果说 → LLM 预训练充分
    "COMPOSITIONAL": "LOW",     # 成分说 → LLM 预训练充分
    "FOLKLORE":      "MEDIUM",  # 民俗说 → 需要舆情证据参与，避免无证据直答
}

# 低置信度分类阈值：< 此值时风险自动升级 HIGH
CLASSIFY_CONFIDENCE_FLOOR = 0.6

# 复杂命题阈值：超过此长度风险升级 HIGH
COMPLEX_CLAIM_CHAR_THRESHOLD = 80


@dataclass
class RiskAssessment:
    """风险评估结果（写入黑板留痕，供论文 §4 消融对比）。"""
    base_risk: RiskLevel          # 从 claim_type 得到的基础风险
    final_risk: RiskLevel         # 应用升级规则后的最终风险
    route: RouteDecision          # 最终路由
    upgrade_reasons: list         # 风险升级原因（调试 + 可审计）
    claim_type: ClaimType
    classify_confidence: float
    claim_length: int

    def as_dict(self) -> dict:
        return {
            "base_risk": self.base_risk,
            "final_risk": self.final_risk,
            "route": self.route,
            "upgrade_reasons": self.upgrade_reasons,
            "claim_type": self.claim_type,
            "classify_confidence": self.classify_confidence,
            "claim_length": self.claim_length,
        }


# ---------------------------------------------------------------------
# 风险评估主函数
# ---------------------------------------------------------------------

def assess_risk(claim: str, classification: ClassifyResult) -> RiskAssessment:
    """
    给定命题 + 分类结果，输出风险等级与路由决策。

    决策树：
      1. base_risk = CLAIM_TYPE_BASE_RISK[primary]
      2. 升级条件（任一命中则 final_risk 提升到 HIGH）：
         a) classify_confidence < 0.6
         b) len(claim) > 80
      3. 路由：final_risk == LOW → LIGHT_CHECK，否则 → FULL_CTAEW
    """
    base = CLAIM_TYPE_BASE_RISK.get(classification.primary, "HIGH")
    final: RiskLevel = base
    upgrade_reasons = []

    # 规则 a：分类置信度低
    if classification.primary_confidence < CLASSIFY_CONFIDENCE_FLOOR:
        final = "HIGH"
        upgrade_reasons.append(
            f"classify_confidence={classification.primary_confidence:.2f} < {CLASSIFY_CONFIDENCE_FLOOR}"
        )

    # 规则 a2：高风险次分类命中时也升级，避免 EFFICACY+NOVEL_TREND 走轻路径
    high_secondary_types = {"INTERACTION", "DOSAGE", "POPULATION", "NOVEL_TREND", "UNKNOWN"}
    if classification.secondary in high_secondary_types and classification.secondary_confidence >= 0.4:
        final = "HIGH"
        upgrade_reasons.append(
            f"secondary={classification.secondary}({classification.secondary_confidence:.2f})"
        )

    # 规则 b：命题复杂度
    claim_len = len(claim)
    if claim_len > COMPLEX_CLAIM_CHAR_THRESHOLD:
        final = "HIGH"
        upgrade_reasons.append(f"claim_length={claim_len} > {COMPLEX_CLAIM_CHAR_THRESHOLD}")

    route: RouteDecision = "LIGHT_CHECK" if final == "LOW" else "FULL_CTAEW"

    assessment = RiskAssessment(
        base_risk=base,
        final_risk=final,
        route=route,
        upgrade_reasons=upgrade_reasons,
        claim_type=classification.primary,
        classify_confidence=classification.primary_confidence,
        claim_length=claim_len,
    )

    logger.info(
        f"🚦 [RiskRouter] claim_type={classification.primary} base={base} "
        f"final={final} → {route}" +
        (f" [升级：{'; '.join(upgrade_reasons)}]" if upgrade_reasons else "")
    )
    return assessment


# ---------------------------------------------------------------------
# Fast-Path：单 LLM 直答（给低风险命题用）
# ---------------------------------------------------------------------

FAST_PATH_SYSTEM = """你是一位资深医学科普顾问，同时兼任谣言核查官。用户给出一条"健康命题"，请严格输出 JSON 核查结果。

【4 类 verdict】
- 属实：主流医学证据支持该说法
- 谣言：主流医学证据否定该说法
- 误导：部分属实但有重要限定/过度推广/夸大
- 尚无定论：缺乏高质量研究无法定性

【输出 JSON 严格格式】
{
  "verdict": "<属实|谣言|误导|尚无定论>",
  "belief_score": <-1.0 ~ +1.0 的信念分：+1 完全支持，-1 完全反驳，0 中立>,
  "confidence": <0.0 ~ 1.0 的自信度>,
  "reasoning": "<≤40 字的核心判断依据>",
  "markdown_report": "<完整 Markdown 核查报告，600-800 字，必须包含三个二级标题：## 📌 核查结论 / ## 🔬 医学原理解析 / ## ⚠️ 实用建议>"
}

【核查纪律】
- 报告末尾加免责声明："以上为健康科普内容，仅供参考，不构成临床建议。"
- 不编造 IARC 等级、RCT 数据；不确定时就说"目前证据有限"
- markdown_report 里用 > blockquote 引用标注核查定性
"""


async def run_fast_path(claim: str, risk_assessment: RiskAssessment) -> Dict[str, Any]:
    """
    单 LLM 快速核查路径。返回与 CTAEW judge packet 兼容的字段。

    设计理由：低风险类型（CAUSAL/COMPOSITIONAL）上 single-LLM 宽松准确率
    接近 100%，用 CTAEW 全流程反而会过度保守弃权（见 ablation）。Fast-Path 复用
    LLM pre-training 知识，80s → 2s。
    """
    try:
        resp = await client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {"role": "system", "content": FAST_PATH_SYSTEM},
                {"role": "user", "content": f"命题：{claim.strip()}"},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.error(f"[FastPath] LLM 异常，降级 UNKNOWN verdict: {e}")
        data = {
            "verdict": "尚无定论",
            "belief_score": 0.0,
            "confidence": 0.0,
            "reasoning": f"fast_path_failed: {type(e).__name__}",
            "markdown_report": (
                f"### 🛡️ AI 智能健康核查报告\n\n"
                f"> **【核查定性】** ❓ 尚无定论\n>\n"
                f"> 对「{claim}」的快速核查流程发生异常。\n"
            ),
        }

    # 规范化字段
    verdict = str(data.get("verdict", "尚无定论"))
    if verdict not in ("属实", "谣言", "误导", "尚无定论"):
        verdict = "尚无定论"
    belief = max(-1.0, min(1.0, float(data.get("belief_score", 0.0) or 0.0)))
    confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0)))

    markdown_report = str(data.get("markdown_report", "")).strip()
    if not markdown_report:
        # 兜底模板
        conf_pct = int(round(confidence * 100))
        head = {"属实": "✅ 经核实属实", "谣言": "❌ 纯属谣言", "误导": "⚠️ 存在误导/片面", "尚无定论": "❓ 尚无定论"}
        markdown_report = (
            f"### 🛡️ AI 智能健康核查报告（Fast-Path）\n\n"
            f"#### 📌 核查结论\n\n"
            f"> **【核查定性】** {head.get(verdict, '❓ 尚无定论')}\n>\n"
            f"> **【可信度】** {conf_pct}%\n>\n"
            f"> 针对命题「{claim}」的核查理由：{data.get('reasoning', '—')}\n\n"
            f"> *以上为健康科普内容，仅供参考，不构成临床建议。*\n"
        )

    logger.info(
        f"⚡ [FastPath] claim='{claim[:30]}...' verdict={verdict} "
        f"belief={belief:+.2f} conf={confidence:.2f}"
    )

    # 构造与 CTAEW judge 同结构的 packet（下游 integration.py 零改动兼容）
    return {
        "claim": claim,
        "claim_type": risk_assessment.claim_type,
        "belief_score": round(belief, 3),
        "dissent_score": 0.0,
        "confidence": round(confidence, 3),
        "final_verdict": verdict,
        "per_source_net": {"kg": 0.0, "rag": 0.0, "web": 0.0},
        "advocate_hits": {"kg": 0, "rag": 0, "web": 0},
        "skeptic_hits": {"kg": 0, "rag": 0, "web": 0},
        "evidence_coverage": 0.0,
        "evidence_sufficiency": 0.0,
        "weights": {"kg": 0.0, "rag": 0.0, "web": 0.0},  # Fast-Path 不走证据权重
        "final_markdown_report": markdown_report,
        "debate_highlights": data.get("reasoning", "Fast-Path 单 LLM 直答（低风险路径）"),
        "termination_reason": "FAST_PATH",
        "rounds_completed": 0,
        "total_tool_calls": 0,
        "total_evidence_hits": 0,
        "classification": {
            "primary": risk_assessment.claim_type,
            "primary_confidence": risk_assessment.classify_confidence,
        },
        "risk_assessment": risk_assessment.as_dict(),
    }


# ═══════════════════════════════════════════════════════
# 轻量风险路由 — 供 graph_engine wrapper 使用
# ═══════════════════════════════════════════════════════

async def route_rumor_risk(query: str) -> tuple:
    """
    快速评估辟谣查询的风险等级，不进入完整 CTAEW 流程。
    同时考虑主分类和次分类（如 EFFICACY+NOVEL_TREND → HIGH）。
    返回 (risk_level, claim_type):
      - risk_level: "LOW" / "MEDIUM" / "HIGH"
      - claim_type: 主分类类型
    """
    try:
        from .claim_classifier import classify_claim
        classification = await classify_claim(query)
        assessment = assess_risk(query, classification)
        return assessment.final_risk, classification.primary
    except Exception:
        return "MEDIUM", "GENERAL"
