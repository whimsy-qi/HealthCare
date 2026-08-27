"""
Phase 3: 辟谣跨模型辩论实验
==========================
400 条医疗健康谣言，对比 Single / Vote-3 / Debate-Cross

用法:
  python experiments/rumor_debate_experiment.py --limit 50   # 快速测试
  python experiments/rumor_debate_experiment.py --limit 0    # 全量 400
"""
import sys, json, time, asyncio, os, re, argparse
sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))
from openai import AsyncOpenAI

# ── 模型客户端 ──
DS  = ("deepseek-v4-pro", AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_API_BASE")), {"thinking":{"type":"disabled"}})
QW  = ("qwen-max",        AsyncOpenAI(api_key=os.getenv("DASHSCOPE_API_KEY"), base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"), {})
GLM = ("glm-5.1",         AsyncOpenAI(api_key=os.getenv("GLM_API_KEY"), base_url=os.getenv("GLM_API_BASE")), {"thinking":{"type":"disabled"}})

MODELS = {"deepseek": DS, "qwen": QW, "glm": GLM}
SEM = asyncio.Semaphore(2)

# ── Prompts ──
SINGLE_PROMPT = """你是医疗事实核查专家。请判定以下健康说法的真实性。

【判定标准】
- 属实：该说法已被权威科学证据证实
- 谣言：该说法已被权威科学证据否定
- 误导：该说法部分真实但表述有误导性，或缺乏关键限定条件
- 尚无定论：科学界目前对该说法没有明确共识，证据相互矛盾或不充分

【输出格式】仅输出一个词：属实、谣言、误导 或 尚无定论。不要解释。"""

DEBATE_ADVOCATE = """你是医疗事实核查中的"辩护方"。你的任务是找到支持该说法的证据和合理推论。
即使说法存疑，你也要尽力为它辩护——强调支持证据、指出反驳证据的局限性。

【输出格式】
【我的判定】属实/谣言/误导/尚无定论
【辩护意见】2-3句话，引用你认为支持该说法的证据"""

DEBATE_SKEPTIC = """你是医疗事实核查中的"质疑方"。你的任务是找到反对该说法的证据和逻辑漏洞。
即使说法有一定道理，你也要尽力质疑——强调反驳证据、指出支持证据的缺陷。

【输出格式】
【我的判定】属实/谣言/误导/尚无定论
【质疑意见】2-3句话，引用你认为反驳该说法的证据"""

JUDGE_PROMPT = """你是医疗事实核查的终审法官。请综合辩护方和质疑方的意见，做出最终判定。

辩护方说：{advocate}
质疑方说：{skeptic}

请综合双方观点，给出最终判定。

【输出格式】仅输出一个词：属实、谣言、误导 或 尚无定论。不要解释。"""


# ── 工具函数 ──
async def call_llm(mid, client, extra, messages, max_tokens=200):
    for attempt in range(3):
        try:
            kwargs = dict(model=mid, messages=messages, temperature=0.0, max_tokens=max_tokens)
            if extra: kwargs["extra_body"] = extra
            resp = await client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt < 2: await asyncio.sleep(2 ** attempt)
    return f"[ERR]"

def extract_verdict(text):
    """从文本提取判定词"""
    for v in ["尚无定论", "属实", "谣言", "误导"]:
        if v in text: return v
    return "?"

def loose_match(pred, gt):
    """宽松匹配：属实/误导 vs 谣言"""
    true_like = {"属实", "误导"}
    false_like = {"谣言"}
    if pred in true_like and gt in true_like: return True
    if pred in false_like and gt in false_like: return True
    if pred == "尚无定论" or gt == "尚无定论": return pred == gt
    return pred == gt


# ── 单模型 ──
async def run_single(questions):
    results = []
    mid, client, extra = DS
    for i, q in enumerate(questions):
        async with SEM:
            raw = await call_llm(mid, client, extra,
                [{"role":"system","content":SINGLE_PROMPT},
                 {"role":"user","content":f"请判定以下健康说法：{q['claim']}"}], 20)
        pred = extract_verdict(raw)
        gt = q.get("ground_truth_verdict", "?")
        results.append({"id":q.get("case_id",i), "claim":q["claim"], "gt":gt, "pred":pred,
            "correct":pred==gt, "loose_correct":loose_match(pred,gt), "mode":"single"})
        if (i+1) % 80 == 0:
            acc = sum(1 for r in results if r["correct"])/len(results)
            print(f"  Single [{len(results)}/{len(questions)}] acc={acc:.1%}", flush=True)
    return results


# ── Vote-3 ──
async def run_vote(questions):
    results = []
    voters = [DS, QW, GLM]
    for i, q in enumerate(questions):
        async with SEM:
            tasks = [call_llm(mid, cl, ex,
                [{"role":"system","content":SINGLE_PROMPT},
                 {"role":"user","content":f"请判定：{q['claim']}"}], 20)
                for mid, cl, ex in voters]
            raws = await asyncio.gather(*tasks)
        preds = [extract_verdict(r) for r in raws]
        # 多数投票
        from collections import Counter
        tally = Counter(preds)
        top, cnt = tally.most_common(1)[0]
        final = top if cnt >= 2 else preds[0]  # tiebreak: DeepSeek
        gt = q.get("ground_truth_verdict", "?")
        results.append({"id":q.get("case_id",i), "claim":q["claim"], "gt":gt, "pred":final,
            "votes":preds, "correct":final==gt, "loose_correct":loose_match(final,gt), "mode":"vote"})
        if (i+1) % 80 == 0:
            acc = sum(1 for r in results if r["correct"])/len(results)
            print(f"  Vote-3 [{len(results)}/{len(questions)}] acc={acc:.1%}", flush=True)
    return results


# ── Debate-Cross ──
async def run_debate(questions):
    results = []
    adv_mid, adv_cl, adv_ex = DS    # Advocate = DeepSeek
    skp_mid, skp_cl, skp_ex = QW    # Skeptic = Qwen
    jdg_mid, jdg_cl, jdg_ex = GLM   # Judge = GLM-5.1

    for i, q in enumerate(questions):
        # Step 1: Advocate + Skeptic 并发
        async with SEM:
            adv_task = call_llm(adv_mid, adv_cl, adv_ex,
                [{"role":"system","content":DEBATE_ADVOCATE},
                 {"role":"user","content":f"请为以下说法辩护：{q['claim']}"}], 200)
            skp_task = call_llm(skp_mid, skp_cl, skp_ex,
                [{"role":"system","content":DEBATE_SKEPTIC},
                 {"role":"user","content":f"请质疑以下说法：{q['claim']}"}], 200)
            adv_raw, skp_raw = await asyncio.gather(adv_task, skp_task)

        adv_verdict = extract_verdict(adv_raw)
        skp_verdict = extract_verdict(skp_raw)

        # Step 2: Judge 综合
        judge_input = f"说法：{q['claim']}\n\n辩护方：{adv_raw[:400]}\n\n质疑方：{skp_raw[:400]}"
        async with SEM:
            jdg_raw = await call_llm(jdg_mid, jdg_cl, jdg_ex,
                [{"role":"system","content":"你是医疗事实核查终审法官。综合双方意见，输出最终判定：属实/谣言/误导/尚无定论。仅输出一个词。"},
                 {"role":"user","content":judge_input}], 20)
        final = extract_verdict(jdg_raw)

        gt = q.get("ground_truth_verdict", "?")
        disagree = (adv_verdict != skp_verdict)
        results.append({"id":q.get("case_id",i), "claim":q["claim"], "gt":gt, "pred":final,
            "advocate":adv_verdict, "skeptic":skp_verdict, "disagree":disagree,
            "correct":final==gt, "loose_correct":loose_match(final,gt), "mode":"debate"})

        if (i+1) % 80 == 0:
            acc = sum(1 for r in results if r["correct"])/len(results)
            disagree_rate = sum(1 for r in results if r["disagree"])/len(results)
            print(f"  Debate [{len(results)}/{len(questions)}] acc={acc:.1%} disagree={disagree_rate:.1%}", flush=True)
    return results


# ── 主函数 ──
async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    # 加载数据
    with open("experiments/data/rumor_eval_400.jsonl", "r", encoding="utf-8") as f:
        questions = [json.loads(line) for line in f if line.strip()]
    if args.limit > 0: questions = questions[:args.limit]
    print(f"辟谣实验: {len(questions)} 条")

    out_dir = "experiments/results/phase3"
    os.makedirs(out_dir, exist_ok=True)
    summary = {}

    for mode, label, runner in [
        ("single", "Single-DeepSeek", run_single),
        ("vote", "Vote-3", run_vote),
        ("debate", "Debate-Cross", run_debate),
    ]:
        print(f"\n{'='*50}\n  {label}\n{'='*50}")
        t0 = time.time()
        res = await runner(questions)
        el = time.time() - t0
        acc = sum(1 for r in res if r["correct"]) / len(res)
        loose_acc = sum(1 for r in res if r["loose_correct"]) / len(res)
        print(f"  DONE: acc={acc:.1%} loose={loose_acc:.1%} {el:.0f}s")

        with open(f"{out_dir}/{mode}.json", "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "accuracy": acc, "loose_accuracy": loose_acc,
                "total": len(res), "correct": sum(1 for r in res if r["correct"]),
                "elapsed": el, "results": res}, f, ensure_ascii=False, indent=2)
        summary[mode] = {"accuracy": acc, "loose_accuracy": loose_acc}

    with open(f"{out_dir}/summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}\n  Phase 3 结果\n{'='*50}")
    for mk, m in summary.items():
        print(f"  {mk:<10s}: acc={m['accuracy']:.1%} loose={m['loose_accuracy']:.1%}")


if __name__ == "__main__":
    asyncio.run(main())
