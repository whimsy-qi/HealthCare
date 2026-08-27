# backend/core/evidence.py
"""
统一证据链 Schema。所有 agent 向这里靠拢，前端按这一份契约渲染。

source_id 协议（字符串前缀）：
  kg:<node_id>                 - Neo4j 节点
  doc:<collection>#<chunk_id>  - 向量库 / PDF chunk
  web:<url_hash>               - 公网检索片段
  pubmed:<pmid>                - PubMed 文献摘要 / 条目
  image:<region_id>            - 多模态视觉提取
  profile:<field>              - 患者档案字段
"""
from typing import TypedDict, List, Literal, Optional


# 受控关系词表（防 LLM 编造同义关系）
RELATION_VOCAB = {
    # 用药
    "禁忌于",       # 药物 - 疾病/症状
    "相互作用",     # 药物 - 药物
    "适应症",       # 药物 - 疾病
    "不良反应",     # 药物 - 症状
    "档案命中",     # 患者档案 - 风险
    # 症状/疾病
    "可能提示",     # 症状 - 疾病
    "推荐就诊",     # 疾病 - 科室
    "观察特征",     # 主症状 - 槽位值（位置/性质/持续/诱因）
    "诊断方向",     # 症状群 - 候选疾病
    "辩论裁决",     # MADDx 鉴别诊断结论
    # 辟谣
    "声称功效",     # 谣言主张 - 功效
    "实际事实",     # 谣言对象 - 真实结论
    "支持依据",     # 命题 - advocate 证据
    "反驳依据",     # 命题 - skeptic 证据
    "命题分类",     # 命题 - claim_type
    "风险等级",     # 命题 - risk tier
    "幻觉裁定",     # 答复 - hallucination guard action
    # 报告解读
    "偏高",         # 指标 - 数值
    "偏低",         # 指标 - 数值
    "阳性",         # 指标 - 状态
    "阴性",         # 指标 - 状态
    "异常",         # 指标 - 状态（兜底）
    "参考依据",     # 指标 - 临床指南/文献
}


class EvidenceTriple(TypedDict, total=False):
    head: str
    relation: str
    tail: str
    source_id: str
    confidence: float       # 0.0-1.0
    tail_type: Optional[str]  # Disease / Symptom / Drug / Department


class EvidenceRef(TypedDict, total=False):
    ref_id: str
    type: Literal["kg", "pdf", "web", "pubmed", "image", "profile"]
    label: str              # 给前端展示用，如 "用药安全指南 P17"
    locator: dict           # {"node_id":"n42"} / {"doc":"xxx.pdf","page":17} / {"url":"..."}
    snippet: Optional[str]  # 原文片段（≤300字），可空
    evidence_role: Literal["evidence", "constraint", "background"]
    citation_allowed: bool


class ReasoningStep(TypedDict, total=False):
    step: int
    actor: str              # "med_extractor" / "med_pharmacist" / "rumor_judge"
    action: str             # "提取药物实体" / "查询KG禁忌" / "综合裁决"
    input_summary: str      # ≤80字
    output_summary: str     # ≤80字
    cited_refs: List[str]   # 本步引用的 ref_id 列表


class EvidenceChain(TypedDict, total=False):
    triples: List[EvidenceTriple]
    reasoning_path: List[ReasoningStep]
    refs: List[EvidenceRef]
    final_claim: str
    confidence: float


# ==========================================
# 辅助构造与校验
# ==========================================
def build_chain(
    triples: Optional[List[EvidenceTriple]] = None,
    reasoning_path: Optional[List[ReasoningStep]] = None,
    refs: Optional[List[EvidenceRef]] = None,
    final_claim: str = "",
    confidence: float = 1.0,
) -> EvidenceChain:
    chain = {
        "triples": triples or [],
        "reasoning_path": reasoning_path or [],
        "refs": refs or [],
        "final_claim": final_claim,
        "confidence": float(confidence),
    }
    return validate_triple_refs(chain)


def dedupe_refs(refs: List[EvidenceRef]) -> List[EvidenceRef]:
    """按 ref_id 去重，保留首次出现的版本。"""
    seen = set()
    out = []
    for r in refs:
        rid = r.get("ref_id")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def validate_triple_refs(chain: EvidenceChain) -> EvidenceChain:
    """剔除 source_id 不在 refs 池里的 triple（防 LLM 幻觉）。"""
    valid_ids = {r.get("ref_id") for r in chain.get("refs", [])}
    if not valid_ids:
        return chain
    chain["triples"] = [
        t for t in chain.get("triples", [])
        if t.get("source_id") in valid_ids
    ]
    return chain
