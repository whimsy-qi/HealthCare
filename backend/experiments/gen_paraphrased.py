"""
🪞 Generate 25 paraphrased rumor variants
==========================================

读取 experiments/data/rumor_eval_seed.jsonl（25 例），用 LLM 生成语义保持
但表述不同的改写版（口语化、变换语序、加冗余前缀等）。

每条改写变体的 ground truth 与原版完全一致（verdict / type / alts），
只把 case_id 改为 "rumor_001_v" 风格、claim 改为改写后的版本。

输出：experiments/data/rumor_eval_paraphrased.jsonl
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm_client import shared_client as client  # noqa: E402


PARAPHRASE_SYSTEM = """你是一位精通中文医疗问答的改写专家。请把给定的【医疗谣言/问题】改写成
**语义完全不变、但表述自然不同**的另一句话。

【硬性要求】
1. 核心医学命题（涉及的实体、机制、关系）不能改变。
2. 不要换成完全不同的问题；不要扩缩范围。
3. 风格更口语化 / 加自然冗余 / 改语序，但仍是同一个事实问题。
4. 直接输出改写句，不要解释，不要引号。

示例：
  原句："长期熬夜会增加癌症风险"
  改写："经常通宵熬夜是不是会让得癌症的概率变高？"

  原句："微波炉加热食物会致癌"
  改写："用微波炉热菜，会不会让饭菜变得致癌？"

  原句："吃味精会掉头发"
  改写："听说做菜放味精会脱发，是真的吗？"
"""


async def paraphrase(claim: str) -> str:
    resp = await client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": PARAPHRASE_SYSTEM},
            {"role": "user",   "content": f"原句：{claim}\n改写："},
        ],
        temperature=0.8,
        max_tokens=120,
    )
    out = resp.choices[0].message.content.strip()
    # 清理潜在的引号 / 前缀
    for prefix in ("改写：", "改写句：", "改写后："):
        if out.startswith(prefix):
            out = out[len(prefix):].strip()
    return out.strip().strip("\"'「」「」")


async def main():
    src = Path(__file__).resolve().parent / "data" / "rumor_eval_seed.jsonl"
    dst = Path(__file__).resolve().parent / "data" / "rumor_eval_paraphrased.jsonl"

    cases = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            cases.append(json.loads(line))

    print(f"载入 {len(cases)} 例原始谣言。开始并发改写…")

    sem = asyncio.Semaphore(4)
    async def _one(case):
        async with sem:
            try:
                new_claim = await paraphrase(case["claim"])
            except Exception as e:
                print(f"  ⚠️ {case['case_id']} 改写失败: {e}; 沿用原文")
                new_claim = case["claim"]
            return {**case,
                    "case_id": case["case_id"] + "_v",
                    "claim": new_claim,
                    "_origin_claim": case["claim"]}

    paraphrased = await asyncio.gather(*(_one(c) for c in cases))

    with open(dst, "w", encoding="utf-8") as f:
        for c in paraphrased:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"\n改写完成。落盘 → {dst}\n")
    print("前 6 例对照：")
    for c in paraphrased[:6]:
        print(f"  原：{c['_origin_claim']}")
        print(f"  改：{c['claim']}\n")


if __name__ == "__main__":
    asyncio.run(main())
