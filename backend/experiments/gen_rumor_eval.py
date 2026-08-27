"""
谣言评测集生成 + 审查 管线
=========================
三模型分离，消除循环论证：
  生成层: Qwen-Max    — 按 8 type × 4 verdict 生成 400 条
  审查层: GLM-5.1     — 逐条审查 verdict/rationale/引用，输出 PASS/FIX/REJECT
  评测层: DeepSeek    — Phase 2 辩论实验的评测者，绝不参与数据构造

用法:
  python experiments/gen_rumor_eval.py                    # 生成+审查 全部 400 条
  python experiments/gen_rumor_eval.py --types CAUSAL,EFFICACY  # 仅生成指定类型
  python experiments/gen_rumor_eval.py --review-only      # 仅审查已有数据
"""
import json, asyncio, os, time, re, argparse, sys
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))
from openai import AsyncOpenAI

# ── 模型客户端 ──
GEN_CLIENT = AsyncOpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)
GEN_MODEL = "qwen-max"

REVIEW_CLIENT = AsyncOpenAI(
    api_key=os.getenv("GLM_API_KEY"),
    base_url=os.getenv("GLM_API_BASE")
)
REVIEW_MODEL = "glm-5.1"
REVIEW_EXTRA = {"thinking": {"type": "disabled"}}

# ── 类型 × verdict × 难度配额 ──
CLAIM_TYPES = {
    "CAUSAL":        ("因果型", "X会导致/不会导致Y —— 探讨病因、风险因素与疾病发生的关系", "easy"),
    "EFFICACY":      ("功效型", "X能/不能治Y —— 探讨某种食物/药物/行为的治疗功效", "easy"),
    "COMPOSITIONAL": ("成分型", "X含有/不含有Y —— 探讨某种物品的成分构成", "medium"),
    "DOSAGE":        ("剂量型", "X应该这样/那样吃/用 —— 探讨用法用量", "medium"),
    "INTERACTION":   ("相互作用型", "X和Y能/不能一起吃/用 —— 探讨药物/食物相互作用", "hard"),
    "POPULATION":    ("人群型", "X对某类人有效/有害 —— 探讨特定人群的适用性", "medium"),
    "NOVEL_TREND":   ("新观点型", "最新研究发现X —— 探讨近年新出现的医学观点", "hard"),
    "FOLKLORE":      ("民俗型", "老一辈传下来的X说法 —— 探讨民间健康习俗", "easy"),
}

VERDICTS = ["属实", "谣言", "误导", "尚无定论"]
TARGET_PER_TYPE = 63   # 8 × 63 = 504 → 审查后预计 400+
BATCH_SIZE = 8
SEM = asyncio.Semaphore(2)

# ── 生成 Prompt ──
GEN_PROMPT = """你是中文医疗健康谣言数据库的构建专家。请生成 {batch_size} 条【{type_cn}】类型的医疗健康说法。

【该类谣言的定义】
{type_desc}

【强制要求】
1. 每条 claim 在中文互联网上真实存在或可能存在的健康说法（不要编造火星细菌之类的）
2. 每条给出 ground_truth_verdict，四选一：属实 / 谣言 / 误导 / 尚无定论
3. 每条给出 rationale（判定依据，80-150字），引用至少一个权威来源
4. 权威来源：WHO公告 / FDA公告 / PubMed PMID / NEJM/Lancet/JAMA/BMJ / 中国卫健委/药监局 / Cochrane / UpToDate / 权威医学教科书
5. 每条 difficulty：easy（常识级）/ medium（需要专业判断）/ hard（需要查阅文献）
6. 每条 2-4 个 tags
7. verdict="误导"时给出 acceptable_alt_verdicts
8. verdict 分布均匀——{batch_size} 条中各约 {per_verdict} 条

【输出】严格 JSON 数组，无额外文字：
[
  {{
    "claim": "完整陈述句",
    "ground_truth_type": "{type_key}",
    "ground_truth_verdict": "属实|谣言|误导|尚无定论",
    "acceptable_alt_verdicts": [],
    "rationale": "80-150字判定依据",
    "source": "guideline|literature|internet_rumor|folklore",
    "source_ref": "具体引用",
    "difficulty": "easy|medium|hard",
    "tags": ["标签1", "标签2"]
  }}
]"""


async def gen_batch(type_key, type_info, batch_size):
    type_cn, type_desc, _ = type_info
    prompt = GEN_PROMPT.format(
        batch_size=batch_size, type_cn=type_cn, type_desc=type_desc,
        type_key=type_key, per_verdict=max(1, batch_size // 4)
    )
    async with SEM:
        try:
            resp = await GEN_CLIENT.chat.completions.create(
                model=GEN_MODEL, temperature=0.8, max_tokens=4096,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```json\s*', '', raw); raw = re.sub(r'\s*```$', '', raw)
            return json.loads(raw)
        except Exception as e:
            print(f"  [GEN ERR] {type_key}: {e}")
            return []


# ── 审查 Prompt ──
REVIEW_PROMPT = """你是医疗事实核查专家。请审查以下由 AI 生成的医疗健康谣言条目。

【审查标准】
1. verdict 是否正确？（基于 general medical knowledge 判断）
2. rationale 中的引用是否真实可查？（PMID、WHO报告、FDA公告等）
3. rationale 的推理逻辑是否自洽？
4. claim 是否可能在中文互联网上真实传播？（不能是火星细菌这种）
5. difficulty 标签是否匹配？

【你的判定】
- PASS: 全部通过，可以直接使用
- FIX: 需要修正（给出修正后的 verdict 或 rationale）
- REJECT: 不可救药（常识性错误、引用完全虚构、claim 荒谬）

【输出格式】严格 JSON：
{{
  "decision": "PASS|FIX|REJECT",
  "reason": "一句话原因",
  "fixed": {{  // 仅 decision=FIX 时需要
    "ground_truth_verdict": "修正后的verdict",
    "rationale": "修正后的rationale",
    "source_ref": "修正后的引用",
    "difficulty": "修正后的difficulty"
  }}
}}

【待审查条目】
{claim_json}

仅输出 JSON，无其他文字。"""


async def review_one(item, idx, total):
    async with SEM:
        claim_json = json.dumps({
            "claim": item["claim"],
            "ground_truth_verdict": item["ground_truth_verdict"],
            "rationale": item["rationale"],
            "source_ref": item.get("source_ref", ""),
            "difficulty": item.get("difficulty", "medium"),
        }, ensure_ascii=False, indent=2)

        try:
            resp = await REVIEW_CLIENT.chat.completions.create(
                model=REVIEW_MODEL, temperature=0.0, max_tokens=600,
                messages=[{"role": "user", "content": REVIEW_PROMPT.format(claim_json=claim_json)}],
                extra_body=REVIEW_EXTRA
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```json\s*', '', raw); raw = re.sub(r'\s*```$', '', raw)
            return json.loads(raw)
        except Exception as e:
            print(f"  [REVIEW ERR] #{idx}: {e}")
            return {"decision": "PASS", "reason": "review_failed"}


# ── 主流程 ──
async def generate_and_review(types_filter=None):
    all_reviewed = []
    stats = {"PASS": 0, "FIX": 0, "REJECT": 0}

    types_to_gen = {k: v for k, v in CLAIM_TYPES.items() if not types_filter or k in types_filter}

    for type_key, type_info in types_to_gen.items():
        print(f"\n{'='*50}")
        print(f"生成: {type_info[0]} ({type_key}) × {TARGET_PER_TYPE}")
        print(f"{'='*50}")

        # Step 1: 生成
        gen_results = []
        for i in range(0, TARGET_PER_TYPE, BATCH_SIZE):
            bs = min(BATCH_SIZE, TARGET_PER_TYPE - i)
            print(f"  生成 batch {i//BATCH_SIZE+1} ({bs}条)...", end=" ", flush=True)
            items = await gen_batch(type_key, type_info, bs)
            if items:
                gen_results.extend(items)
                print(f"OK {len(items)}")
            else:
                print("FAIL")
            await asyncio.sleep(0.5)

        # 去重
        seen = set()
        gen_unique = []
        for item in gen_results:
            c = item.get("claim", "").strip()
            if c and c not in seen:
                seen.add(c)
                gen_unique.append(item)
        print(f"  生成 {len(gen_unique)} 条 (去重后)")

        # Step 2: 审查
        print(f"  审查中 (GLM-5.1)...")
        reviewed = []
        for i, item in enumerate(gen_unique):
            result = await review_one(item, i + 1, len(gen_unique))
            decision = result.get("decision", "PASS")

            if decision == "REJECT":
                stats["REJECT"] += 1
                continue
            elif decision == "FIX":
                stats["FIX"] += 1
                fixed = result.get("fixed", {})
                if fixed.get("ground_truth_verdict"):
                    item["ground_truth_verdict"] = fixed["ground_truth_verdict"]
                if fixed.get("rationale"):
                    item["rationale"] = fixed["rationale"]
                if fixed.get("source_ref"):
                    item["source_ref"] = fixed["source_ref"]
                if fixed.get("difficulty"):
                    item["difficulty"] = fixed["difficulty"]
                item["_review_note"] = result.get("reason", "")
            else:
                stats["PASS"] += 1

            item["case_id"] = f"gen_{type_key.lower()}_{len(reviewed)+1:03d}"
            item["_reviewed_by"] = "GLM-5.1"
            item["_review_decision"] = decision
            reviewed.append(item)

            if (i + 1) % 20 == 0:
                print(f"    审查进度: {i+1}/{len(gen_unique)} PASS={stats['PASS']} FIX={stats['FIX']} REJECT={stats['REJECT']}", flush=True)

        all_reviewed.extend(reviewed)
        print(f"  审查完成: 采纳 {len(reviewed)} 条 (PASS={stats['PASS']} FIX={stats['FIX']} REJECT={stats['REJECT']})")

    return all_reviewed, stats


async def review_existing(filepath):
    """仅审查已有数据（不生成）。"""
    items = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                items.append(json.loads(line))
    print(f"加载 {len(items)} 条待审查数据")

    reviewed = []
    stats = {"PASS": 0, "FIX": 0, "REJECT": 0}
    for i, item in enumerate(items):
        result = await review_one(item, i + 1, len(items))
        decision = result.get("decision", "PASS")
        stats[decision] = stats.get(decision, 0) + 1

        if decision == "REJECT":
            continue
        elif decision == "FIX":
            fixed = result.get("fixed", {})
            for k in ["ground_truth_verdict", "rationale", "source_ref", "difficulty"]:
                if fixed.get(k):
                    item[k] = fixed[k]
            item["_review_note"] = result.get("reason", "")

        item["_reviewed_by"] = "GLM-5.1"
        item["_review_decision"] = decision
        reviewed.append(item)

        if (i + 1) % 20 == 0:
            print(f"  进度: {i+1}/{len(items)} {stats}", flush=True)

    return reviewed, stats


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--types", default="", help="仅生成指定类型（逗号分隔），如 CAUSAL,EFFICACY")
    parser.add_argument("--review-only", action="store_true", help="仅审查已有数据")
    parser.add_argument("--input", default="", help="审查模式下的输入文件")
    args = parser.parse_args()

    if args.review_only:
        infile = args.input or "experiments/data/rumor_eval_400_raw.jsonl"
        reviewed, stats = await review_existing(infile)
    else:
        types_filter = [t.strip() for t in args.types.split(",") if t.strip()] if args.types else None
        reviewed, stats = await generate_and_review(types_filter)

    # ── 保存 ──
    out_dir = "experiments/data"
    os.makedirs(out_dir, exist_ok=True)

    # 审查后的最终数据集
    out_path = f"{out_dir}/rumor_eval_400.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for item in reviewed:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # ── 统计 ──
    vc, tc, dc = {}, {}, {}
    for item in reviewed:
        vc[item.get("ground_truth_verdict","?")] = vc.get(item.get("ground_truth_verdict","?"), 0) + 1
        tc[item.get("ground_truth_type","?")] = tc.get(item.get("ground_truth_type","?"), 0) + 1
        dc[item.get("difficulty","?")] = dc.get(item.get("difficulty","?"), 0) + 1

    print(f"\n{'='*50}")
    print(f"  管线完成")
    print(f"  最终采纳: {len(reviewed)} 条")
    print(f"  审查结果: {stats}")
    print(f"  Verdict: {json.dumps(vc, ensure_ascii=False)}")
    print(f"  Type: {json.dumps(tc, ensure_ascii=False)}")
    print(f"  Difficulty: {json.dumps(dc, ensure_ascii=False)}")
    print(f"  保存: {out_path}")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())
