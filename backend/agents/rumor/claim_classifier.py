"""
R1 — Claim Classifier (8 类医疗谣言分类器)
==========================================
把用户输入的谣言命题归类到 8 类之一（+ UNKNOWN 兜底）。
保留 top-2 候选：若 Judge 最终 belief 接近阈值，可用 top-2 权重做二次裁决。

这是 D9 的关键前置：错误的分类会把"热点谣言"误判走 KG 高权路径，
导致 Judge 看到大量空命中，最终误判为"尚无定论"。
"""
import json
import logging
from typing import Optional, List, Tuple, Dict, Any

from core.llm_client import shared_client as client, FAST_MODEL
from .weight_policy import ClaimType, ALL_CLAIM_TYPES

logger = logging.getLogger("Rumor.ClaimClassifier")

MODEL = FAST_MODEL

# ---------------------------------------------------------------------
# 分类 prompt —— 关键是让 LLM 看到足够多的判例（Few-Shot）
# ---------------------------------------------------------------------

SYSTEM_PROMPT = """你是一位医疗谣言分类专家。请把用户给出的"谣言命题"归入下列 8 类之一。
你还必须给出 top-2 候选类型 + 置信度，用于下游的加权证据检索。

【8 类定义与识别特征】

1. CAUSAL（因果说）——"做 X 会导致 Y" / "吃 X 会得 Y"
   关键词：导致、引起、得、诱发、造成、致癌、致病
   例："长期熬夜会得白血病" / "微波炉加热食物致癌"

2. COMPOSITIONAL（成分说）——"X 含有成分 Y，所以..."
   关键词：含、成分、其中的、含量、化学物质名
   例："苹果核含氰化物会中毒" / "不粘锅含特氟龙致癌"

3. EFFICACY（功效说）——"X 能治疗/预防 Y"
   关键词：能治、可以治、预防、根治、治好、消除、改善
   例："维生素 C 能预防感冒" / "喝盐水能治新冠"

4. DOSAGE（剂量说）——和"吃多少/喝多少"有关
   关键词：一天 N 次、N 粒、N 克、过量、大量、微量
   例："一天吃 10 粒维 C 才能防感冒" / "每天 8 杯水"

5. INTERACTION（联用说）——"X 和 Y 一起..."
   关键词：同时、一起、同服、配合、和...一起
   例："头孢加酒立即死亡" / "柚子不能和降压药同服"

6. POPULATION（人群说）——特定人群能/不能做某事
   关键词：孕妇、老人、小孩、婴幼儿、哺乳期、糖尿病人
   例："孕妇不能吃海鲜" / "糖尿病人不能吃任何水果"

7. NOVEL_TREND（热点说）——近期流行偏方、网红食疗、时事相关
   关键词：新冠/疫情、某明星、网红、最近、刚出、直播间
   例："某明星同款抗癌神方" / "疫情期间喝板蓝根预防"

8. FOLKLORE（民俗说）——"老祖宗说"/地方传统/非科学民间经验
   关键词：老祖宗、民间、偏方、传统、发汗、捂汗、绿豆汤
   例："发烧要捂汗" / "骨头汤能补钙"

【如果真的无法归类】→ UNKNOWN（兜底）

【判类优先级】
· 如果命题同时触发多类特征，按此优先级判 primary：
  INTERACTION > DOSAGE > COMPOSITIONAL > POPULATION > CAUSAL > EFFICACY > NOVEL_TREND > FOLKLORE
· 例："孕妇一天不能喝超过 2 杯咖啡" 同时是 POPULATION + DOSAGE → primary=DOSAGE，secondary=POPULATION

【严格输出 JSON】
{
  "primary": "<8 类或 UNKNOWN 之一>",
  "primary_confidence": 0.0~1.0,
  "secondary": "<可选的次选类别，无则 null>",
  "secondary_confidence": 0.0~1.0,
  "reasoning": "<20 字内说明判类依据>"
}

【Few-Shot】
命题：微波炉加热食物会致癌
输出：{"primary":"CAUSAL","primary_confidence":0.9,"secondary":"NOVEL_TREND","secondary_confidence":0.15,"reasoning":"典型因果说，关键词致癌"}

命题：苹果核里的氰苷会中毒
输出：{"primary":"COMPOSITIONAL","primary_confidence":0.92,"secondary":"CAUSAL","secondary_confidence":0.4,"reasoning":"成分名触发，兼含因果"}

命题：柚子不能和降压药一起吃
输出：{"primary":"INTERACTION","primary_confidence":0.95,"secondary":null,"secondary_confidence":0.0,"reasoning":"明确药食联用"}

命题：孕妇每天一杯咖啡会流产
输出：{"primary":"DOSAGE","primary_confidence":0.7,"secondary":"POPULATION","secondary_confidence":0.65,"reasoning":"剂量+人群双特征"}

命题：发烧要捂汗才能退烧
输出：{"primary":"FOLKLORE","primary_confidence":0.88,"secondary":"EFFICACY","secondary_confidence":0.5,"reasoning":"民间偏方型"}

命题：疫情期间喝板蓝根能预防新冠
输出：{"primary":"NOVEL_TREND","primary_confidence":0.85,"secondary":"EFFICACY","secondary_confidence":0.7,"reasoning":"疫情时事+预防功效"}
"""


# ---------------------------------------------------------------------
# 结果数据结构
# ---------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class ClassifyResult:
    primary: ClaimType
    primary_confidence: float
    secondary: Optional[ClaimType]
    secondary_confidence: float
    reasoning: str

    def as_dict(self) -> dict:
        return {
            "primary": self.primary,
            "primary_confidence": self.primary_confidence,
            "secondary": self.secondary,
            "secondary_confidence": self.secondary_confidence,
            "reasoning": self.reasoning,
        }

    @property
    def is_confident(self) -> bool:
        """分类是否可信。低置信度时 workflow 可能启用 top-2 重审。"""
        return self.primary_confidence >= 0.6

    @property
    def needs_secondary_review(self) -> bool:
        """是否建议二次裁决：top-1 和 top-2 置信度接近（差距 < 0.2）时为真。"""
        if self.secondary is None:
            return False
        return (self.primary_confidence - self.secondary_confidence) < 0.2


# ---------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------

def _normalize_type(raw: Any) -> ClaimType:
    """把 LLM 可能返回的各种变体（大小写/中文同义）归一化到合法 ClaimType。"""
    if not isinstance(raw, str):
        return "UNKNOWN"
    r = raw.strip().upper()
    if r in ALL_CLAIM_TYPES:
        return r  # type: ignore[return-value]
    # 容错映射
    synonyms = {
        "CAUSAL_CLAIM": "CAUSAL", "CAUSE": "CAUSAL",
        "COMPOSITION": "COMPOSITIONAL", "INGREDIENT": "COMPOSITIONAL",
        "EFFECT": "EFFICACY", "TREATMENT": "EFFICACY",
        "DOSE": "DOSAGE", "QUANTITY": "DOSAGE",
        "INTERACT": "INTERACTION",
        "GROUP": "POPULATION", "DEMOGRAPHIC": "POPULATION",
        "TREND": "NOVEL_TREND", "HOT": "NOVEL_TREND", "NEWS": "NOVEL_TREND",
        "FOLK": "FOLKLORE", "TRADITION": "FOLKLORE", "TRADITIONAL": "FOLKLORE",
    }
    return synonyms.get(r, "UNKNOWN")  # type: ignore[return-value]


def _clamp_conf(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


async def classify_claim(query: str) -> ClassifyResult:
    """对谣言命题分类。异常时返回 UNKNOWN（Workflow 会走 uniform 权重兜底）。"""
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"命题：{query.strip()}"},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.error(f"[ClaimClassifier] LLM/JSON 异常，降级 UNKNOWN: {e}")
        return ClassifyResult(
            primary="UNKNOWN", primary_confidence=0.0,
            secondary=None, secondary_confidence=0.0,
            reasoning=f"classify_failed: {type(e).__name__}",
        )

    primary = _normalize_type(data.get("primary"))
    secondary_raw = data.get("secondary")
    secondary = _normalize_type(secondary_raw) if secondary_raw else None

    result = ClassifyResult(
        primary=primary,
        primary_confidence=_clamp_conf(data.get("primary_confidence")),
        secondary=secondary if secondary and secondary != primary else None,
        secondary_confidence=_clamp_conf(data.get("secondary_confidence")),
        reasoning=str(data.get("reasoning", ""))[:100],
    )

    logger.info(
        f"🎯 [ClaimClassifier] '{query[:30]}...' → "
        f"{result.primary}({result.primary_confidence:.2f})"
        + (f" / {result.secondary}({result.secondary_confidence:.2f})" if result.secondary else "")
    )
    return result
