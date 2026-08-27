"""
Phase 2: 跨模型辩论实验
=======================
两组 × 三种模式 × 1200 题 CMB-Exam

Group A: DeepSeek-V4-Pro (Proposer) + Qwen-Max (Critic)
Group B: DeepSeek-V4-Pro (Proposer) + GLM-5.1 (Critic)

每组合跑: Vote-3 / Debate-1round / Debate-2round

Vote-3: DeepSeek + Qwen + GLM-5.1 三人独立盲答 → 多数投票
核心修正（vs 初版）:
  1. Vote-2 分歧时引入 Moderator 终裁，而非无脑取 Proposer
  2. Critic 先盲答再审查，消除锚定效应
  3. 辩论结束后增加独立的 Moderator 终裁节点

用法:
  python experiments/debate_runner.py --group A --mode all --limit 50
  python experiments/debate_runner.py --group A --mode all --limit 0
"""
import sys, json, time, asyncio, os, argparse, re
sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))
from openai import AsyncOpenAI

# ═══════════════════════════════════════════════════════
# 模型配置
# ═══════════════════════════════════════════════════════

def _ds():  return AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_API_BASE"))
def _qw():  return AsyncOpenAI(api_key=os.getenv("DASHSCOPE_API_KEY"), base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
def _glm(): return AsyncOpenAI(api_key=os.getenv("GLM_API_KEY"), base_url=os.getenv("GLM_API_BASE"))

PROPOSER = {"model_id":"deepseek-v4-pro", "client":_ds(), "extra":{"thinking":{"type":"disabled"}}, "name":"DeepSeek-V4-Pro"}
MODERATOR = {"model_id":"deepseek-v4-pro", "client":_ds(), "extra":{"thinking":{"type":"disabled"}}, "name":"DeepSeek-V4-Pro(裁判)"}

CRITICS = {
    "A": {"model_id":"qwen-max", "client":_qw(), "extra":{}, "name":"Qwen-Max"},
    "B": {"model_id":"glm-5.1",  "client":_glm(), "extra":{"thinking":{"type":"disabled"}}, "name":"GLM-5.1"},
}

SEM = asyncio.Semaphore(2)

# ═══════════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════════

SOLO = """你是临床医生。阅读题目，给出最可能的答案。
【强制规则】仅输出答案字母。单选题一个字母，多选题用逗号分隔（如 A,C,D）。不要解释。"""

SOLO_REASON = """你是临床医生。阅读题目，给出最可能的答案和一句推理依据。
格式：【答案】A 【依据】一句话"""

MODERATOR_PROMPT = """你是科主任。两位医生对一个病例有分歧，请综合双方意见做出最终裁决。
格式：【最终答案】A（单选题一个字母）/ A,C,D（多选题逗号分隔）"""

DEBATE_PROPOSER = """你是住院医师（{p}）。对面是主治医师（{c}）。
你的任务是先独立给出答案和依据，然后回应主治医师的质疑，最后给出最终答案。

第一次回复格式: 【我的答案】A 【依据】一句话
回应质疑格式: 【最终答案】A"""

DEBATE_CRITIC = """你是主治医师（{c}），正在审查住院医师（{p}）的判断。
先独立给出你的判断，然后审查住院医师的答案。

格式: 【我的答案】A 【审查意见】2-3句分析。同意说"同意"，不同意指出具体问题。"""


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

async def call_llm(client, model_id, messages, extra, max_tokens=150, max_retries=3):
    """带指数退避的 LLM 调用。"""
    last_err = None
    for attempt in range(max_retries):
        try:
            kwargs = dict(model=model_id, messages=messages, temperature=0.0, max_tokens=max_tokens)
            if extra: kwargs["extra_body"] = extra
            resp = await client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                await asyncio.sleep(wait)
    return f"[ERR:{type(last_err).__name__}]"

def extract_answer(text, options):
    """从文本提取答案字母。只认【标记】格式，不遍历全文。"""
    # 严格匹配【最终答案】或【我的答案】或【答案】
    for tag in ["最终答案","我的答案","答案"]:
        m = re.search(rf'【{tag}】\s*([A-H,，\s]+)', text)
        if m:
            letters = set()
            for ch in m.group(1).upper():
                if ch in options: letters.add(ch)
            if letters: return ''.join(sorted(letters))
    # 最后尝试：取文本最后一个大写字母（很多模型会在结尾写 "答案是 A"）
    last_chars = text.strip().upper()[-5:]
    letters = set()
    for ch in last_chars:
        if ch in options: letters.add(ch)
    return ''.join(sorted(letters)) if letters else "?"

def is_multi(q): return '多选' in q.get('question_type','')

def score(pred, gt, multi):
    if multi:
        return set(pred) == set(gt.replace(',','').replace(' ','').replace('，',''))
    return pred == gt.strip().upper()

def fmt_opts(opts): return '\n'.join(f'{k}. {v[:60]}' for k, v in opts.items())[:600]

# ═══════════════════════════════════════════════════════
# 断点续跑
# ═══════════════════════════════════════════════════════

def load_checkpoint(path):
    """加载已保存的部分结果。返回 (results_list, completed_ids_set) 或 (None, set())"""
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            completed = {r['id'] for r in data.get('results', [])}
            return data.get('results', []), completed
        except:
            pass
    return None, set()

def save_checkpoint(path, results):
    """保存部分结果，每 100 题调用一次。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"results": results, "count": len(results), "checkpoint_ts": time.strftime("%H:%M:%S")},
                  f, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# 1. Vote-3（三模型独立盲答 → 多数投票）
# ═══════════════════════════════════════════════════════

async def run_vote(questions, critic_cfg, checkpoint_path=None):
    """Vote-3: DeepSeek + Critic + 第三模型（另一组的Critic）"""
    other_key = "B" if critic_cfg is CRITICS["A"] else "A"
    third = CRITICS[other_key]
    voters = [
        (PROPOSER["client"], PROPOSER["model_id"], PROPOSER["extra"], PROPOSER["name"]),
        (critic_cfg["client"], critic_cfg["model_id"], critic_cfg["extra"], critic_cfg["name"]),
        (third["client"], third["model_id"], third["extra"], third["name"]),
    ]

    # ── 断点续跑 ──
    results, completed_ids = load_checkpoint(checkpoint_path) if checkpoint_path else (None, set())
    if results is None:
        results = []

    for idx, q in enumerate(questions):
        if q['id'] in completed_ids:
            continue
        multi = is_multi(q)
        opts = fmt_opts(q['option'])
        user = f"{q['question'][:400]}\n{opts}"

        async with SEM:
            tasks = [call_llm(cl, mid, [{"role":"system","content":SOLO},{"role":"user","content":user}], ex, 10)
                     for cl, mid, ex, _ in voters]
            raws = await asyncio.gather(*tasks)
        answers = [extract_answer(raw, q['option']) for raw in raws]

        from collections import Counter
        tally = Counter(answers)
        top_ans, top_count = tally.most_common(1)[0]
        final = top_ans if top_count >= 2 else answers[0]
        consensus = "majority" if top_count >= 2 else "tiebreak"

        results.append({"id":q["id"],"gt":q["answer"],"final":final,
            "answers":answers,"voter_names":[v[3] for v in voters],
            "consensus":consensus,
            "correct":score(final,q['answer'],multi),"mode":"vote"})

        if len(results) % 100 == 0:
            acc = sum(1 for r in results if r["correct"]) / len(results)
            majority_rate = sum(1 for r in results if r["consensus"]=="majority") / len(results)
            print(f"  Vote-3 [{len(results)}/{len(questions)}] acc={acc:.1%} majority={majority_rate:.1%}", flush=True)
            if checkpoint_path: save_checkpoint(checkpoint_path, results)

    if checkpoint_path: save_checkpoint(checkpoint_path, results)
    return results


# ═══════════════════════════════════════════════════════
# 2. Debate（盲答 → 审查 → 防守 → Moderator 终裁）
# ═══════════════════════════════════════════════════════

async def run_debate(questions, critic_cfg, rounds=1, checkpoint_path=None):
    p_name = PROPOSER["name"]; c_name = critic_cfg["name"]

    results, completed_ids = load_checkpoint(checkpoint_path) if checkpoint_path else (None, set())
    if results is None:
        results = []

    for idx, q in enumerate(questions):
        if q['id'] in completed_ids:
            continue
        multi = is_multi(q); opts = fmt_opts(q['option']); base = f"题目：{q['question'][:400]}\n选项：{opts}"

        # ── Step 0: 双方真正盲答（修正锚定效应）──
        async with SEM:
            p_blind = call_llm(PROPOSER["client"], PROPOSER["model_id"],
                [{"role":"system","content":SOLO_REASON},{"role":"user","content":base}], PROPOSER["extra"], 80)
            c_blind = call_llm(critic_cfg["client"], critic_cfg["model_id"],
                [{"role":"system","content":SOLO_REASON},{"role":"user","content":base}], critic_cfg["extra"], 80)
            p_blind_raw, c_blind_raw = await asyncio.gather(p_blind, c_blind)

        p_ans_0 = extract_answer(p_blind_raw, q['option'])
        c_ans_0 = extract_answer(c_blind_raw, q['option'])

        # 如果盲答一致且 non-empty → 直接结束
        if p_ans_0 == c_ans_0 and p_ans_0 != "?":
            results.append({"id":q["id"],"gt":q["answer"],"final":p_ans_0,
                "prop_ans":p_ans_0,"crit_ans":c_ans_0,"disagree":False,"rounds_used":0,
                "correct":score(p_ans_0,q['answer'],multi),"mode":f"debate-{rounds}r"})
            if (idx+1) % 200 == 0:
                acc = sum(1 for r in results if r["correct"]) / len(results)
                print(f"  Debate-{rounds}r [{len(results)}/{len(questions)}] acc={acc:.1%}", flush=True)
            continue

        # ── 辩论轮次 ──
        history = [
            f"[住院医师初诊] {p_blind_raw[:300]}",
            f"[主治医师初诊] {c_blind_raw[:300]}",
        ]
        current_p_response = p_blind_raw
        p_current = p_ans_0

        for r in range(rounds):
            # Critic 审查（每轮看到 Proposer 最新的回应，而非首轮盲答）
            critic_prompt = DEBATE_CRITIC.format(p=p_name, c=c_name)
            critic_input = f"住院医师当前的判断：{current_p_response[:300]}\n\n{base}"
            async with SEM:
                c_review = await call_llm(critic_cfg["client"], critic_cfg["model_id"],
                    [{"role":"system","content":critic_prompt},{"role":"user","content":critic_input}],
                    critic_cfg["extra"], 150)
            history.append(f"[主治医师审查-第{r+1}轮] {c_review[:300]}")

            # Proposer 防守
            prop_prompt = DEBATE_PROPOSER.format(p=p_name, c=c_name)
            prop_input = f"你的初步判断：{p_blind_raw[:200]}\n\n主治医师审查意见：{c_review[:300]}\n\n请回应并给出最终答案。\n{base}"
            async with SEM:
                p_defense = await call_llm(PROPOSER["client"], PROPOSER["model_id"],
                    [{"role":"system","content":prop_prompt},{"role":"user","content":prop_input}],
                    PROPOSER["extra"], 150)
            history.append(f"[住院医师回应-第{r+1}轮] {p_defense[:300]}")

            # 更新状态，供下一轮 Critic 审查
            current_p_response = p_defense
            p_current = extract_answer(p_defense, q['option'])

        # ── Moderator 终裁（修正：不再是 Proposer 说了算）──
        mod_input = (
            f"病例：{q['question'][:300]}\n选项：{opts}\n\n"
            + "\n".join(history)
            + "\n\n请综合以上双方的全部发言，给出你的最终裁决。"
        )
        mod_raw = await call_llm(MODERATOR["client"], MODERATOR["model_id"],
            [{"role":"system","content":MODERATOR_PROMPT},{"role":"user","content":mod_input}],
            MODERATOR["extra"], 50)
        final_ans = extract_answer(mod_raw, q['option'])

        results.append({"id":q["id"],"gt":q["answer"],"final":final_ans,
            "prop_ans":p_ans_0,"crit_ans":c_ans_0,
            "final_prop_ans":p_current,"disagree":p_ans_0!=c_ans_0,
            "correct":score(final_ans,q['answer'],multi),
            "rounds_used":rounds,"mode":f"debate-{rounds}r"})

        if len(results) % 100 == 0:
            acc = sum(1 for r in results if r["correct"]) / len(results)
            disagree_rate = sum(1 for r in results if r.get("disagree")) / len(results)
            print(f"  Debate-{rounds}r [{len(results)}/{len(questions)}] acc={acc:.1%} disagree={disagree_rate:.1%}", flush=True)
            if checkpoint_path: save_checkpoint(checkpoint_path, results)

    if checkpoint_path: save_checkpoint(checkpoint_path, results)
    return results


# ═══════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--group", default="A", choices=["A","B"])
    p.add_argument("--mode", default="all", choices=["vote","debate1","debate2","all"])
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    critic = CRITICS[args.group]
    print(f"Group {args.group}: {PROPOSER['name']} + {critic['name']} | Mode: {args.mode}")

    with open("experiments/data/cmb_3cat_1200.json", "r", encoding="utf-8") as f:
        questions = json.load(f)
    if args.limit > 0: questions = questions[:args.limit]
    print(f"题目数: {len(questions)}")

    out_dir = f"experiments/results/phase2/group_{args.group}"
    os.makedirs(out_dir, exist_ok=True)

    modes = {"vote":"vote","debate1":"debate1","debate2":"debate2"}
    if args.mode != "all": modes = {args.mode: args.mode}

    summary = {"group":args.group,"proposer":PROPOSER["name"],"critic":critic["name"]}

    for mk, mlabel in modes.items():
        print(f"\n{'='*60}\n  {mlabel.upper()}\n{'='*60}")
        t0 = time.time()

        ckpt_path = f"{out_dir}/.checkpoint_{mk}.json"

        if mk == "vote":
            res = await run_vote(questions, critic, checkpoint_path=ckpt_path)
        elif mk == "debate1":
            res = await run_debate(questions, critic, rounds=1, checkpoint_path=ckpt_path)
        elif mk == "debate2":
            res = await run_debate(questions, critic, rounds=2, checkpoint_path=ckpt_path)

        el = time.time()-t0
        acc = sum(1 for r in res if r["correct"])/len(res)
        disagree = sum(1 for r in res if r.get("disagree"))
        print(f"  DONE: acc={acc:.1%} ({sum(1 for r in res if r['correct'])}/{len(res)}) disagree={disagree} {el:.0f}s")

        # 完整结果保存后删除断点文件
        if os.path.exists(ckpt_path):
            os.remove(ckpt_path)

        out = f"{out_dir}/{mk}.json"
        with open(out,"w",encoding="utf-8") as f:
            json.dump({"group":args.group,"mode":mk,"accuracy":acc,"total":len(res),
                "correct":sum(1 for r in res if r["correct"]),"disagree":disagree,
                "elapsed_sec":el,"results":res}, f, ensure_ascii=False, indent=2)
        summary[mk] = {"accuracy":acc,"correct":sum(1 for r in res if r["correct"]),"total":len(res),"disagree":disagree}

    with open(f"{out_dir}/summary.json","w",encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}\n  Group {args.group} 结果\n{'='*60}")
    for mk in summary:
        if isinstance(summary[mk], dict) and "accuracy" in summary[mk]:
            m = summary[mk]
            print(f"  {mk:<10s}: acc={m['accuracy']:.1%} ({m['correct']}/{m['total']}) disagree={m['disagree']}")

# ═══════════════════════════════════════════════════════
# 可复用 Runner 类 — 供 graph_engine.py 及其他模块调用
# ═══════════════════════════════════════════════════════

class VoteRunner:
    """三模型多数投票。graph_engine 中可按意图直接调用。"""

    @staticmethod
    async def run(query: str, models: list = None) -> str:
        """
        对单个自然语言 query 执行三模型投票。
        models: ["deepseek","qwen","glm"] 或任意子集，默认全部三模型。
        返回: 综合投票后的最终答案文本。
        """
        if models is None:
            models = ["deepseek", "qwen", "glm"]

        model_map = {
            "deepseek": PROPOSER,
            "qwen": CRITICS["A"],
            "glm": CRITICS["B"],
        }
        voters = [(model_map[m], m) for m in models if m in model_map]
        if not voters:
            return "[VoteRunner] 无可用模型"

        async with asyncio.Semaphore(3):
            tasks = [
                call_llm(v["client"], v["model_id"],
                    [{"role": "system", "content": SOLO},
                     {"role": "user", "content": query}],
                    v["extra"], max_tokens=200)
                for v, _ in voters
            ]
            raws = await asyncio.gather(*tasks)

        # 简单多数投票：每个模型输出作为一票，取出现最多的回答
        from collections import Counter
        answers = [raw.strip()[:200] for raw in raws]
        tally = Counter(answers)
        top_answer, top_count = tally.most_common(1)[0]

        if top_count >= 2:
            return top_answer
        # 全部不同 → 取 Proposer 的（最准模型）
        return answers[0]


class DebateRunner:
    """两模型对抗辩论。graph_engine 中可按意图直接调用。"""

    @staticmethod
    async def run(query: str, proposer: str = "deepseek",
                  critic: str = "glm", rounds: int = 1) -> str:
        """
        对单个自然语言 query 执行 Proposer↔Critic 辩论。
        proposer/critic: "deepseek" / "qwen" / "glm"
        rounds: 辩论轮数（1 或 2）
        返回: Moderator 终裁后的最终答案文本。
        """
        model_map = {
            "deepseek": PROPOSER,
            "qwen": CRITICS["A"],
            "glm": CRITICS["B"],
        }
        prop = model_map.get(proposer, PROPOSER)
        crit = model_map.get(critic, CRITICS["B"])

        p_name = prop["name"]
        c_name = crit["name"]

        # Step 0: 双方盲答
        async with asyncio.Semaphore(2):
            p_blind = call_llm(prop["client"], prop["model_id"],
                [{"role": "system", "content": SOLO_REASON},
                 {"role": "user", "content": query}],
                prop["extra"], max_tokens=200)
            c_blind = call_llm(crit["client"], crit["model_id"],
                [{"role": "system", "content": SOLO_REASON},
                 {"role": "user", "content": query}],
                crit["extra"], max_tokens=200)
            p_blind_raw, c_blind_raw = await asyncio.gather(p_blind, c_blind)

        # 盲答一致 → 直接返回
        p_ans_0 = p_blind_raw.strip()[:200]
        c_ans_0 = c_blind_raw.strip()[:200]
        if p_ans_0.lower() == c_ans_0.lower():
            return p_ans_0

        # 辩论轮次
        current_p = p_blind_raw
        history = [
            f"[住院医师初诊] {p_blind_raw[:300]}",
            f"[主治医师初诊] {c_blind_raw[:300]}",
        ]

        for r in range(rounds):
            # Critic 审查
            critic_input = (
                f"住院医师当前的判断：{current_p[:300]}\n\n{query}"
            )
            critic_prompt = DEBATE_CRITIC.format(p=p_name, c=c_name)
            async with asyncio.Semaphore(1):
                c_review = await call_llm(crit["client"], crit["model_id"],
                    [{"role": "system", "content": critic_prompt},
                     {"role": "user", "content": critic_input}],
                    crit["extra"], max_tokens=200)
            history.append(f"[主治医师审查-第{r+1}轮] {c_review[:300]}")

            # Proposer 防守
            prop_prompt = DEBATE_PROPOSER.format(p=p_name, c=c_name)
            prop_input = (
                f"你的初步判断：{p_blind_raw[:200]}\n\n"
                f"主治医师审查意见：{c_review[:300]}\n\n请回应并给出最终答案。\n{query}"
            )
            async with asyncio.Semaphore(1):
                p_defense = await call_llm(prop["client"], prop["model_id"],
                    [{"role": "system", "content": prop_prompt},
                     {"role": "user", "content": prop_input}],
                    prop["extra"], max_tokens=200)
            history.append(f"[住院医师回应-第{r+1}轮] {p_defense[:300]}")
            current_p = p_defense

        # Moderator 终裁
        mod_input = (
            f"{query}\n\n" + "\n".join(history)
            + "\n\n请综合以上双方的全部发言，给出你的最终裁决。"
        )
        mod_raw = await call_llm(MODERATOR["client"], MODERATOR["model_id"],
            [{"role": "system", "content": MODERATOR_PROMPT},
             {"role": "user", "content": mod_input}],
            MODERATOR["extra"], max_tokens=300)
        return mod_raw.strip()


if __name__ == "__main__":
    asyncio.run(main())
