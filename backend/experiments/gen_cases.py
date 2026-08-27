"""
MADDx D8 评测数据集生成助手
============================
用 LLM 批量生成「与 Neo4j KG 词表对齐」的合成病例，再人工过审后合并到
experiments/data/maddx_eval_100.jsonl。

策略：
  1. 提供 per-department 的「疾病白名单」（必须出现在 KG 中的疾病）作为 ground_truth.primary 候选；
  2. LLM 按 TestCase schema 产出 JSON；
  3. 本脚本做结构校验 + 去重（case_id 冲突、症状完全重复的 case 会被丢弃）；
  4. 输出到 `data/maddx_eval_generated.jsonl`，由人工核对后再合并主数据集。

用法：
  python -m experiments.gen_cases --per-dept 15 --out experiments/data/maddx_eval_generated.jsonl
  # 生成 6 × 15 = 90 例候选，人工筛到 88 例与种子 12 例合并成 100。

注意：
  - 该脚本只负责「候选生成」。最终数据集必须人工把关，防止 LLM 幻觉疾病名。
  - 所有疾病名请与 scripts/setup_neo4j.py 中导入的 KG 词表对齐。
"""
import os
import sys
import json
import asyncio
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.llm_client import shared_client as client

logger = logging.getLogger("D8.GenCases")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s | %(message)s',
    datefmt='%H:%M:%S',
)

# ------------------------------------------------------------------
# 疾病白名单（必须与 Neo4j KG 中导入的 Disease 节点名完全一致）
# ------------------------------------------------------------------
DEPARTMENT_DISEASES: Dict[str, List[str]] = {
    "神经": ["偏头痛", "紧张型头痛", "丛集性头痛", "脑卒中", "短暂性脑缺血发作",
             "癫痫", "帕金森病", "重症肌无力", "多发性硬化", "面神经炎"],
    "呼吸": ["肺炎", "急性支气管炎", "慢性阻塞性肺疾病", "支气管哮喘", "肺结核",
             "上呼吸道感染", "肺栓塞", "气胸", "肺癌", "急性会厌炎"],
    "消化": ["急性阑尾炎", "急性胃肠炎", "消化性溃疡", "慢性胃炎", "胃食管反流病",
             "胆囊炎", "胆石症", "急性胰腺炎", "肠易激综合征", "炎症性肠病"],
    "心血管": ["稳定型心绞痛", "不稳定型心绞痛", "急性心肌梗死", "心力衰竭",
               "心房颤动", "高血压", "病毒性心肌炎", "主动脉夹层", "心律失常",
               "风湿性心脏病"],
    "内分泌": ["2 型糖尿病", "1 型糖尿病", "糖尿病酮症酸中毒", "甲状腺功能亢进",
               "甲状腺功能减退", "库欣综合征", "原发性醛固酮增多症",
               "嗜铬细胞瘤", "高钙血症", "痛风"],
    "泌尿": ["急性膀胱炎", "尿路感染", "急性肾盂肾炎", "肾结石", "输尿管结石",
             "前列腺增生", "前列腺炎", "慢性肾脏病", "肾病综合征", "膀胱癌"],
}

GEN_SYSTEM = """你是一位临床数据合成专家。请按给定 JSON schema 生成合成临床病例，
用于鉴别诊断系统的评测。

要求：
1. ground_truth.primary 必须严格从提供的【疾病白名单】里选一个；
2. acceptable_tier2 从同科室白名单里选 2 个（与 primary 不同的邻近疾病）；
3. symptoms 至少 3 条，写明 duration_days 和 severity (mild/moderate/severe)；
4. 病例要贴近真实临床：主诉 ≤ 20 字，病史字段合理；
5. age/gender 与疾病流行病学一致（如甲亢偏好年轻女性、COPD 偏好老年男性）；
6. 不得生成白名单以外的 primary 疾病；不得输出任何解释性文字。

严格输出 JSON：
{"cases": [<TestCase>, <TestCase>, ...]}
每个 TestCase 字段：
  case_id, department, age, gender, chief_complaint, symptoms:[{name,duration_days,severity}],
  history:[...], ground_truth:{primary, acceptable_tier2:[..], icd10},
  source:"synthetic", notes:""
"""


async def gen_for_department(dept: str, n: int, start_idx: int) -> List[Dict[str, Any]]:
    """让 LLM 为指定科室生成 n 条合成病例。"""
    whitelist = DEPARTMENT_DISEASES[dept]
    prefix = {
        "神经": "neuro", "呼吸": "resp", "消化": "gi",
        "心血管": "cv", "内分泌": "endo", "泌尿": "uro",
    }[dept]

    user = (
        f"【科室】{dept}\n"
        f"【疾病白名单】{json.dumps(whitelist, ensure_ascii=False)}\n"
        f"【需生成病例数】{n}\n"
        f"【case_id 前缀】{prefix}_（请从 {prefix}_{start_idx:03d} 开始，顺序递增）\n"
        f"请生成 {n} 条覆盖不同疾病的合成病例，ground_truth.primary 在白名单里均匀分布。"
    )
    resp = await client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": GEN_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.6,   # 提高多样性
        response_format={"type": "json_object"},
    )
    try:
        data = json.loads(resp.choices[0].message.content)
        cases = data.get("cases", [])
    except json.JSONDecodeError as e:
        logger.warning(f"[{dept}] JSON parse failed: {e}")
        return []

    # 结构校验：白名单命中
    valid = []
    for c in cases:
        gt = (c.get("ground_truth") or {}).get("primary", "")
        if gt not in whitelist:
            logger.warning(f"[{dept}] reject (primary '{gt}' not in whitelist): {c.get('case_id')}")
            continue
        if not c.get("symptoms"):
            continue
        c["department"] = dept
        c["source"] = "synthetic"
        valid.append(c)
    logger.info(f"[{dept}] accept {len(valid)}/{len(cases)}")
    return valid


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-dept", type=int, default=15, help="每科室生成多少例")
    ap.add_argument("--start-idx", type=int, default=3,
                    help="case_id 起始序号（默认 3，因为 _001/_002 已在种子集）")
    ap.add_argument("--out", default="experiments/data/maddx_eval_generated.jsonl")
    args = ap.parse_args()

    all_cases: List[Dict] = []
    tasks = [gen_for_department(dept, args.per_dept, args.start_idx)
             for dept in DEPARTMENT_DISEASES.keys()]
    results = await asyncio.gather(*tasks)
    for batch in results:
        all_cases.extend(batch)

    # 去重（按 case_id）
    seen = set()
    unique = []
    for c in all_cases:
        cid = c.get("case_id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        unique.append(c)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for c in unique:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    logger.info(f"生成 {len(unique)} 条候选病例 → {out_path}")
    logger.info("下一步：人工审核后，cat 到 experiments/data/maddx_eval_seed.jsonl")


if __name__ == "__main__":
    asyncio.run(main())
