"""
🧠 Intent Ontology — 双层意图本体
=================================

设计参考：
- 袁嵩等《基于 LLM 与多智能体协同的医疗对话机制研究》
  (计算机技术与发展 2025.8)：行为×属性 双层意图划分。
- 谌文佳等《嵌入意图识别的医疗健康问答文本语义分类模型》
  (数据分析与知识发现 2025.2)：意图作为知识嵌入到下游 prompt。

本系统采用三轴正交意图模型：
    (Domain) × (Act) × (Attr)
       ↓        ↓       ↓
     路由    意图    内容
   决定走     决定 prompt 偏重
   哪个 agent

与原版袁嵩论文的差异：
    保留你已有的 6 类 Domain（成熟、graph_engine 依赖），
    新增正交的 Act × Attr 二维内容轴，仅影响 prompt 偏重，
    不影响路由决策 → 0 风险叠加。
"""
from typing import Dict, List, Literal

# =============================================================================
# 1. 常量定义
# =============================================================================

# 行为轴（Act）：用户【想做什么】
ACT_LABELS: Dict[str, Dict[str, str]] = {
    "ASK":       {"zh": "询问", "desc": "用户主动想了解某个医学知识"},
    "CONFIRM":   {"zh": "确认", "desc": "用户已有判断、希望求证或核实"},
    "SEEK_HELP": {"zh": "求助", "desc": "用户处于不适状态、需要处理方案"},
    "DEBUNK":    {"zh": "辟谣", "desc": "用户对某说法表怀疑、想求真假"},
    "ANALYZE":   {"zh": "分析", "desc": "用户提供数据/报告、想要解读"},
}
ActIntent = Literal["ASK", "CONFIRM", "SEEK_HELP", "DEBUNK", "ANALYZE", ""]
ACT_KEYS: List[str] = list(ACT_LABELS.keys())

# 属性轴（Attr）：用户【关注什么内容】
ATTR_LABELS: Dict[str, Dict[str, str]] = {
    "CAUSE":    {"zh": "病因",       "desc": "致病机制、风险因素、相关研究"},
    "SYMPTOM":  {"zh": "临床表现",    "desc": "症状、体征、典型表现"},
    "BASIC":    {"zh": "基础信息",    "desc": "定义、流行病学、术语解释"},
    "CHECKUP":  {"zh": "检查建议",    "desc": "化验、影像、筛查项目"},
    "VISIT":    {"zh": "就医建议",    "desc": "挂哪科、何时去、紧急度"},
    "PREVENT":  {"zh": "预防方式",    "desc": "生活习惯、疫苗、定期体检"},
    "DIAGNOSE": {"zh": "诊断推理",    "desc": "鉴别诊断、可能疾病排查"},
    "CAUTION":  {"zh": "注意事项",    "desc": "禁忌、警示、用药须知、剂量"},
}
AttrIntent = Literal[
    "CAUSE", "SYMPTOM", "BASIC", "CHECKUP",
    "VISIT", "PREVENT", "DIAGNOSE", "CAUTION", ""
]
ATTR_KEYS: List[str] = list(ATTR_LABELS.keys())


# =============================================================================
# 2. ATTR_FOCUS — 注入 agent system prompt 的偏重指令
# =============================================================================

# 每条 ≤ 80 字，避免 prompt 膨胀。所有指令都以"本次咨询的核心需求是 X"为统一句式。
ATTR_FOCUS: Dict[str, str] = {
    "CAUSE":    "本次咨询的核心需求是【病因机制】：请聚焦致病原理、风险因素与相关研究证据，少展开预防/治疗。",
    "SYMPTOM":  "本次咨询的核心需求是【临床表现】：请详细列举典型症状、伴随体征及其严重度分级，便于用户对照。",
    "BASIC":    "本次咨询的核心需求是【基础信息科普】：用通俗语言解释定义、流行病学背景、术语含义，避免堆砌细节。",
    "CHECKUP":  "本次咨询的核心需求是【检查建议】：请给出建议做的化验/影像/筛查项目清单及解读要点。",
    "VISIT":    "本次咨询的核心需求是【就医建议】：请优先告知挂哪个科、是否需要急诊、需要带什么资料。",
    "PREVENT":  "本次咨询的核心需求是【预防干预】：请聚焦生活方式、饮食、疫苗、定期检查等可执行措施。",
    "DIAGNOSE": "本次咨询的核心需求是【诊断推理】：请基于已有信息梳理鉴别诊断思路，列出可能疾病及排查路径。",
    "CAUTION":  "本次咨询的核心需求是【注意事项】：请重点输出禁忌、警示、用药须知、剂量与时机等关键风险点。",
}


# =============================================================================
# 3. 校验 / 工具函数
# =============================================================================

def normalize_act(value: str) -> ActIntent:
    """容错解析 act：大小写不敏感 + 中文别名。非法值返回空串。"""
    if not value:
        return ""
    v = value.strip().upper()
    if v in ACT_LABELS:
        return v  # type: ignore[return-value]
    # 中文别名
    zh_to_en = {meta["zh"]: k for k, meta in ACT_LABELS.items()}
    if value.strip() in zh_to_en:
        return zh_to_en[value.strip()]  # type: ignore[return-value]
    return ""


def normalize_attr(value: str) -> AttrIntent:
    """容错解析 attr。非法值返回空串。"""
    if not value:
        return ""
    v = value.strip().upper()
    if v in ATTR_LABELS:
        return v  # type: ignore[return-value]
    zh_to_en = {meta["zh"]: k for k, meta in ATTR_LABELS.items()}
    if value.strip() in zh_to_en:
        return zh_to_en[value.strip()]  # type: ignore[return-value]
    return ""


def render_attr_focus(attr_intent: str) -> str:
    """根据 attr 返回对应的 prompt 偏重指令。非法/空 → 空串（agent 走默认行为）。"""
    a = normalize_attr(attr_intent)
    if not a:
        return ""
    return f"\n\n【🎯 当次内容侧重】\n{ATTR_FOCUS[a]}\n"


def describe(act: str = "", attr: str = "") -> str:
    """生成人类可读的中文描述，用于 audit_log / 前端 trace。"""
    a = normalize_act(act); t = normalize_attr(attr)
    parts: List[str] = []
    if a:
        parts.append(f"行为={ACT_LABELS[a]['zh']}({a})")
    if t:
        parts.append(f"内容={ATTR_LABELS[t]['zh']}({t})")
    return " · ".join(parts) if parts else "未识别"


# =============================================================================
# 4. Few-Shot 示例库（triage_agent prompt 注入用）
# =============================================================================

# 8 种 attr 各配 1 条最有代表性的示例 + 几条边界 case，让 LLM 看清判定边界
FEWSHOT_EXAMPLES: List[Dict[str, str]] = [
    # —— 8 类 attr 各一条 ——
    {"q": "高血压的病因是什么",          "act": "ASK",       "attr": "CAUSE"},
    {"q": "糖尿病的典型症状有哪些",      "act": "ASK",       "attr": "SYMPTOM"},
    {"q": "什么是乙肝",                  "act": "ASK",       "attr": "BASIC"},
    {"q": "怀孕初期要做哪些检查",        "act": "ASK",       "attr": "CHECKUP"},
    {"q": "感冒该挂什么科",              "act": "ASK",       "attr": "VISIT"},
    {"q": "怎么预防中风",                "act": "ASK",       "attr": "PREVENT"},
    {"q": "我这是不是流感",              "act": "CONFIRM",   "attr": "DIAGNOSE"},
    {"q": "吃头孢能喝酒吗",              "act": "CONFIRM",   "attr": "CAUTION"},
    # —— 5 类 act 各一条（防止偏 ASK） ——
    {"q": "我头疼",                      "act": "SEEK_HELP", "attr": "DIAGNOSE"},
    {"q": "我血压 160 怎么办",           "act": "SEEK_HELP", "attr": "VISIT"},
    {"q": "微波炉加热致癌吗",            "act": "DEBUNK",    "attr": "CAUSE"},
    {"q": "尿酸 520 提示什么",           "act": "ANALYZE",   "attr": "DIAGNOSE"},
    # —— 易混淆边界 ——
    {"q": "高血压怎么预防",              "act": "ASK",       "attr": "PREVENT"},
    {"q": "高血压病因是什么",            "act": "ASK",       "attr": "CAUSE"},
    {"q": "高血压挂什么科",              "act": "ASK",       "attr": "VISIT"},
]


def render_fewshot_block(max_examples: int = 12) -> str:
    """把 few-shot 示例渲染成 prompt 可注入的字符串。"""
    lines = ["【二维意图 Few-Shot 示例（每条都给 act + attr）】"]
    for ex in FEWSHOT_EXAMPLES[:max_examples]:
        lines.append(
            f"  - 「{ex['q']}」  →  act={ex['act']}, attr={ex['attr']}"
        )
    return "\n".join(lines)


# =============================================================================
# 5. 意图驱动的协作模式选择
# =============================================================================

# 协作模式枚举。旧常量保留为别名，避免其它模块的历史字符串立刻失效。
COLLAB_SINGLE_REACT    = "single_react"     # 单 Agent ReAct
COLLAB_SINGLE_REACT_KG = "single_react_kg"  # 单 Agent ReAct + 强制证据检索
COLLAB_FUSION          = "fusion"           # 开放式多模型生成 + 融合
COLLAB_DEBATE          = "debate"           # 对抗式辩论

COLLAB_SINGLE    = COLLAB_SINGLE_REACT
COLLAB_SINGLE_KG = COLLAB_SINGLE_REACT_KG

DEFAULT_PRIMARY_MODEL = ["deepseek"]
DEFAULT_FUSION_MODELS = ["deepseek", "qwen", "glm"]


def _policy(
    mode: str,
    reason: str,
    models: List[str] | None = None,
    *,
    requires_evidence: bool = False,
    allowed_domains: List[str] | None = None,
) -> dict:
    return {
        "mode": mode,
        "models": models or DEFAULT_PRIMARY_MODEL,
        "reason": reason,
        "allowed_domains": allowed_domains or [],
        "requires_evidence": requires_evidence,
    }


# 意图 → 协作模式映射。这里只描述开放式问答策略，不再保留未落地的闭集投票模式。
# key = (act, attr), value = structured policy
COLLAB_MODE_MAP: Dict[tuple, dict] = {
    # 辟谣/事实冲突：交给专线 debate/CTAEW，general 不再复用选择题 VoteRunner。
    ("DEBUNK", "CAUSE"): _policy(
        COLLAB_DEBATE, "辟谣因果类问题需要正反证据交锋", DEFAULT_FUSION_MODELS,
        allowed_domains=["RUMOR_VERIFICATION"],
    ),
    ("DEBUNK", "BASIC"): _policy(
        COLLAB_DEBATE, "辟谣基础事实类问题需要正反证据交锋", DEFAULT_FUSION_MODELS,
        allowed_domains=["RUMOR_VERIFICATION"],
    ),

    # 诊断推理：优先交给症状/MADDx，不在 general 做闭集投票。
    ("SEEK_HELP", "DIAGNOSE"): _policy(
        COLLAB_DEBATE, "诊断求助存在鉴别诊断风险，交给症状辩论链路",
        ["deepseek", "glm"], requires_evidence=True,
        allowed_domains=["SYMPTOM_ANALYSIS"],
    ),

    # 治疗/用药/风险类开放问答：单 Agent 但必须查证。
    ("SEEK_HELP", "CAUTION"): _policy(
        COLLAB_SINGLE_REACT_KG, "治疗或用药求助需要先查证禁忌与指南",
        requires_evidence=True, allowed_domains=["GENERAL_CONSULTATION"],
    ),
    ("SEEK_HELP", "VISIT"): _policy(
        COLLAB_SINGLE_REACT_KG, "就医处理建议需要证据约束",
        requires_evidence=True,
    ),
    ("SEEK_HELP", "CHECKUP"): _policy(
        COLLAB_SINGLE_REACT_KG, "检查建议需要结合指南或知识图谱",
        requires_evidence=True,
    ),
    ("SEEK_HELP", "CAUSE"): _policy(
        COLLAB_SINGLE_REACT_KG, "症状求助下的病因解释需要证据约束",
        requires_evidence=True,
    ),
    ("SEEK_HELP", "SYMPTOM"): _policy(
        COLLAB_SINGLE_REACT_KG, "症状处理建议需要证据约束",
        requires_evidence=True,
    ),

    # 用药/禁忌确认：单 Agent + KG/指南。
    ("CONFIRM", "CAUTION"): _policy(
        COLLAB_SINGLE_REACT_KG, "禁忌或安全确认需要 KG/指南验证",
        requires_evidence=True,
    ),
    ("CONFIRM", "BASIC"): _policy(
        COLLAB_SINGLE_REACT_KG, "医学事实确认需要证据约束",
        requires_evidence=True,
    ),
    ("CONFIRM", "CHECKUP"): _policy(
        COLLAB_SINGLE_REACT_KG, "检查相关确认需要证据约束",
        requires_evidence=True,
    ),

    # 分析类开放问答：用生成融合，不做字符串多数投票。
    ("ANALYZE", "DIAGNOSE"): _policy(
        COLLAB_FUSION, "开放式诊断/报告分析更适合多模型生成融合",
        DEFAULT_FUSION_MODELS, requires_evidence=True,
    ),
    ("ANALYZE", "CHECKUP"): _policy(
        COLLAB_FUSION, "检查解读属于开放式分析任务，使用多模型融合",
        DEFAULT_FUSION_MODELS, requires_evidence=True,
    ),
    ("ANALYZE", "CAUSE"): _policy(
        COLLAB_FUSION, "病因机制分析属于开放式任务，使用多模型融合",
        DEFAULT_FUSION_MODELS, requires_evidence=True,
    ),
    ("ANALYZE", "BASIC"): _policy(
        COLLAB_FUSION, "开放式综合分析使用多模型融合",
        DEFAULT_FUSION_MODELS,
    ),
    ("ANALYZE", "SYMPTOM"): _policy(
        COLLAB_FUSION, "症状模式分析使用多模型融合",
        DEFAULT_FUSION_MODELS, requires_evidence=True,
    ),
    ("ANALYZE", "CAUTION"): _policy(
        COLLAB_FUSION, "风险分析使用多模型融合并保留分歧",
        DEFAULT_FUSION_MODELS, requires_evidence=True,
    ),

    # 科普常识：默认单 Agent ReAct，必要时由模型自主查工具。
    ("ASK", "BASIC"): _policy(COLLAB_SINGLE_REACT, "基础科普走单 Agent ReAct"),
    ("ASK", "PREVENT"): _policy(COLLAB_SINGLE_REACT, "预防科普走单 Agent ReAct"),
    ("ASK", "CAUSE"): _policy(COLLAB_SINGLE_REACT, "病因科普走单 Agent ReAct"),
    ("ASK", "SYMPTOM"): _policy(COLLAB_SINGLE_REACT, "症状科普走单 Agent ReAct"),
    ("ASK", "CHECKUP"): _policy(COLLAB_SINGLE_REACT, "检查科普走单 Agent ReAct"),
    ("ASK", "VISIT"): _policy(COLLAB_SINGLE_REACT, "就医科普走单 Agent ReAct"),
    ("ASK", "CAUTION"): _policy(
        COLLAB_SINGLE_REACT_KG, "注意事项类科普需要证据约束",
        requires_evidence=True,
    ),
}

ACT_FALLBACK_MAP: Dict[str, dict] = {
    "ASK": _policy(COLLAB_SINGLE_REACT, "ASK 未覆盖属性默认走单 Agent ReAct"),
    "CONFIRM": _policy(
        COLLAB_SINGLE_REACT_KG, "CONFIRM 未覆盖属性默认加证据约束",
        requires_evidence=True,
    ),
    "SEEK_HELP": _policy(
        COLLAB_SINGLE_REACT_KG, "SEEK_HELP 未覆盖属性默认加证据约束",
        requires_evidence=True,
    ),
    "ANALYZE": _policy(
        COLLAB_FUSION, "ANALYZE 未覆盖属性默认走开放式融合",
        DEFAULT_FUSION_MODELS, requires_evidence=True,
    ),
    "DEBUNK": _policy(
        COLLAB_DEBATE, "DEBUNK 未覆盖属性默认走辩论/事实核查",
        DEFAULT_FUSION_MODELS, allowed_domains=["RUMOR_VERIFICATION"],
    ),
}


def select_collab_mode(
    act: str,
    attr: str,
    uncertainty: float = 0.0,
    *,
    domain: str = "",
    sub_intent: str = "",
) -> dict:
    """
    根据 Domain × Act × Attr 选择多智能体协作模式。

    返回结构化策略:
    {
        "mode": "single_react"|"single_react_kg"|"fusion"|"debate",
        "models": [...],
        "reason": "...",
        "allowed_domains": [...],
        "requires_evidence": bool
    }
    """
    act_norm = normalize_act(act)
    attr_norm = normalize_attr(attr)

    if uncertainty > 0.3:
        if domain == "RUMOR_VERIFICATION" or act_norm == "DEBUNK":
            return _policy(
                COLLAB_DEBATE,
                f"开放式高不确定辟谣任务转入辩论（uncertainty={uncertainty:.2f}）",
                DEFAULT_FUSION_MODELS,
                allowed_domains=["RUMOR_VERIFICATION"],
            )
        if domain == "SYMPTOM_ANALYSIS" and attr_norm == "DIAGNOSE":
            return _policy(
                COLLAB_DEBATE,
                f"诊断类高不确定任务转入症状辩论（uncertainty={uncertainty:.2f}）",
                ["deepseek", "glm"],
                requires_evidence=True,
                allowed_domains=["SYMPTOM_ANALYSIS"],
            )
        return _policy(
            COLLAB_FUSION,
            f"开放式高不确定任务使用多模型生成融合（uncertainty={uncertainty:.2f}）",
            DEFAULT_FUSION_MODELS,
            requires_evidence=True,
        )

    key = (act_norm, attr_norm)
    if key in COLLAB_MODE_MAP:
        return dict(COLLAB_MODE_MAP[key])

    if act_norm in ACT_FALLBACK_MAP:
        return dict(ACT_FALLBACK_MAP[act_norm])

    return _policy(COLLAB_SINGLE_REACT, "未识别组合默认走单 Agent ReAct")


# =============================================================================
# 6. 调试入口
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("ACT_LABELS:")
    for k, v in ACT_LABELS.items():
        print(f"  {k:<10s} {v['zh']:<5s} - {v['desc']}")
    print()
    print("ATTR_LABELS:")
    for k, v in ATTR_LABELS.items():
        print(f"  {k:<10s} {v['zh']:<6s} - {v['desc']}")
    print()
    print("Render attr focus example (VISIT):")
    print(render_attr_focus("VISIT"))
    print()
    print("Fewshot block:")
    print(render_fewshot_block())
