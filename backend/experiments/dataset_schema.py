"""
MADDx D8 消融实验数据集 Schema
==============================
每条测试用例（test case）的结构化定义。JSONL 格式，每行一个 case。

字段说明：
  case_id        : 全局唯一编号（如 "neuro_001"）
  department     : 分层字段，共 6 类：消化/呼吸/神经/心血管/内分泌/泌尿 各 ~16-17 例
  age / gender   : 流行病学上下文（供 Critic 做 epidemiology_mismatch 判断）
  chief_complaint: 主诉，1 句话
  symptoms       : 结构化症状列表（与 MADDx 输入 schema 一致）
  history        : 病史/既往疾病（可选）
  ground_truth   : 金标诊断
    - primary          : 主诊断名（中文，与 KG 疾病名对齐）
    - acceptable_tier2 : 可接受的 Top-2 候选（作为 Top-3 评价的宽松命中集）
    - icd10            : 可选
  source         : 数据来源（"case_report"/"synthetic"/"MIMIC-adapted" 等），用于论文说明
  notes          : 备注（可选）
"""
from typing import TypedDict, List, Literal, Optional

Department = Literal["消化", "呼吸", "神经", "心血管", "内分泌", "泌尿"]


class GroundTruth(TypedDict, total=False):
    primary: str
    acceptable_tier2: List[str]
    icd10: Optional[str]


class CaseSymptom(TypedDict, total=False):
    name: str
    duration_days: Optional[int]
    severity: Literal["mild", "moderate", "severe"]


class TestCase(TypedDict, total=False):
    case_id: str
    department: Department
    age: int
    gender: Literal["male", "female"]
    chief_complaint: str
    symptoms: List[CaseSymptom]
    history: List[str]
    ground_truth: GroundTruth
    source: str
    notes: str
