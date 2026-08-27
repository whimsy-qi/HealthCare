from __future__ import annotations

import re
from typing import Dict, List

from rag.schema import RagIntent


TERM_EXPANSIONS: Dict[str, List[str]] = {
    "高血压": ["hypertension", "血压", "降压", "诊断标准"],
    "糖尿病": ["diabetes", "血糖", "胰岛素", "二甲双胍"],
    "冠心病": ["coronary artery disease", "冠状动脉", "心绞痛"],
    "心衰": ["心力衰竭", "heart failure", "LVEF"],
    "胸痛": ["急性冠脉综合征", "心肌梗死", "肺栓塞", "主动脉夹层"],
    "二甲双胍": ["metformin", "禁忌", "不良反应", "乳酸酸中毒"],
    "阿司匹林": ["aspirin", "出血", "禁忌", "抗血小板"],
    "布洛芬": ["ibuprofen", "NSAID", "禁忌", "不良反应"],
    "克拉霉素": ["clarithromycin", "CYP3A4", "相互作用"],
    "痛风": ["高尿酸血症", "gout", "尿酸"],
}


def infer_intent(query: str, explicit: str | None = None) -> RagIntent:
    if explicit:
        return explicit  # type: ignore[return-value]
    q = query.lower()
    if any(k in query for k in ["禁忌", "相互作用", "不良反应", "说明书", "用药", "剂量"]) or any(
        k in q for k in ["drug", "dose", "contraindication", "adverse"]
    ):
        return "medication_safety"
    if any(k in query for k in ["最新", "研究", "论文", "临床试验", "疗效", "抗衰老"]) or any(
        k in q for k in ["trial", "pubmed", "study", "research"]
    ):
        return "latest_research"
    if any(k in query for k in ["谣言", "真的假的", "靠谱吗", "偏方", "网传", "辟谣"]):
        return "rumor_check"
    if any(k in query for k in ["症状", "胸痛", "头痛", "发热", "咳嗽", "怎么办", "可能是什么"]):
        return "symptom_dx"
    if any(k in query for k in ["指南", "诊断标准", "治疗建议", "管理", "筛查"]):
        return "guideline_qa"
    return "general"


def normalize_query(query: str) -> str:
    q = re.sub(r"\s+", " ", (query or "").strip())
    expansions: List[str] = []
    for term, values in TERM_EXPANSIONS.items():
        if term in q:
            expansions.extend(values)
    if expansions:
        q = f"{q} {' '.join(dict.fromkeys(expansions))}"
    return q[:500]


def tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    cjk = re.findall(r"[\u4e00-\u9fff]{1,4}", text)
    latin = re.findall(r"[a-z0-9][a-z0-9\-]{1,}", text)
    tokens = latin[:]
    for piece in cjk:
        tokens.append(piece)
        if len(piece) > 1:
            tokens.extend(piece[i : i + 2] for i in range(len(piece) - 1))
    return tokens
