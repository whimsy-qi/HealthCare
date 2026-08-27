"""
基线模型评测脚本
================
对候选 LLM 在 CMB-Exam 执业医师子集上做单模型评测，
计算准确率 + 错误重叠矩阵，选出最互补的两个模型组队辩论。

用法:
  python experiments/baseline_models.py
  python experiments/baseline_models.py --models deepseek,qwen,glm --limit 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

from openai import AsyncOpenAI


# ============================================================
# 模型注册表 —— 所有候选模型 OpenAI-compatible 客户端
# ============================================================

@dataclass
class ModelConfig:
    name: str
    model_id: str
    client: AsyncOpenAI
    temperature: float = 0.0
    extra_body: dict = field(default_factory=dict)

def _build_glm_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=os.getenv("GLM_API_KEY"),
        base_url=os.getenv("GLM_API_BASE"),
    )

def _build_gpt_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=os.getenv("GPT_API_KEY"),
        base_url=os.getenv("GPT_API_BASE"),
    )

# 模型注册
MODELS: Dict[str, ModelConfig] = {
    "deepseek": ModelConfig(
        name="DeepSeek-V4-Pro",
        model_id="deepseek-v4-pro",
        client=AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_API_BASE"),
        ),
        extra_body={"thinking": {"type": "disabled"}},
    ),
    "qwen": ModelConfig(
        name="Qwen-Max",
        model_id="qwen-max",
        client=AsyncOpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    ),
    "glm": ModelConfig(
        name="GLM-5.1",
        model_id="glm-5.1",
        client=_build_glm_client(),
        extra_body={"thinking": {"type": "disabled"}},
    ),
    "gpt": ModelConfig(
        name="GPT-4o",
        model_id="gpt-4o",
        client=_build_gpt_client(),
    ),
}


# ============================================================
# CMB-Exam 数据加载
# ============================================================

async def load_cmb_exam(subset: str = "执业医师", max_q: int = 0) -> List[dict]:
    """
    从本地 JSON 文件加载 CMB-Exam 执业医师选择题。
    返回: [{"id", "question", "options": {"A":"...",...}, "answer": "B"}, ...]
    """
    print(f"正在加载 CMB-Exam | 类别: {subset} ...")
    data_path = os.path.join(BASE_DIR, "experiments", "data", "cmb_3cat_1200.json")
    if not os.path.exists(data_path):
        print(f"  [ERROR] 数据文件不存在: {data_path}")
        return []

    with open(data_path, "r", encoding="utf-8") as f:
        all_data = json.load(f)

    # 格式化为统一结构: id, question, options dict, answer
    questions = []
    for item in all_data:
        q_text = item.get("question", "").strip()
        options = item.get("option", {})
        answer = str(item.get("answer", "")).strip().upper()
        if not q_text or not options or not answer:
            continue
        questions.append({
            "id": item.get("id", f"cmb-{len(questions)}"),
            "question": q_text,
            "options": options,
            "answer": answer,
        })

    if max_q and max_q > 0:
        questions = questions[:max_q]

    print(f"  加载 {len(questions)} 条 '{subset}' 单选题")
    return questions


def _local_fallback_cases() -> List[dict]:
    """如果 CMB 远程不可用，用我们自己的 40 条测试集转选择题格式。"""
    test_path = os.path.join(BASE_DIR, "experiments", "test_cases.json")
    if not os.path.exists(test_path):
        print("  [ERROR] 无可用评测数据")
        return []
    with open(test_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases", [])[:40]
    mc = []
    for c in cases:
        gt = c["ground_truth"]
        diffs = c.get("differentials", [])
        all_opts = [gt] + diffs
        # 构造 4 选项
        import random
        random.seed(hash(c["id"]))
        random.shuffle(all_opts)
        labels = "ABCD"
        options = {labels[i]: all_opts[i] for i in range(min(4, len(all_opts)))}
        # 找到正确答案对应的字母
        answer = ""
        for letter, disease in options.items():
            if disease == gt:
                answer = letter
                break
        mc.append({
            "id": c["id"],
            "question": f"患者主诉：{c['query']}\n可能的诊断是？",
            "options": options,
            "answer": answer,
        })
    print(f"  本地回退数据: {len(mc)} 条")
    return mc


# ============================================================
# 单模型推理
# ============================================================

MEDICAL_MC_SYSTEM = """你是一位经验丰富的临床医生，正在参加医学考试。

【严格输出规则】
- 单选题：仅输出一个正确答案字母（如 A）
- 多选题：输出所有正确选项字母，用逗号分隔（如 A,C,D）
- 不要输出任何解释"""


async def predict_one(model_cfg: ModelConfig, question: str, options: Dict[str, str]) -> str:
    """返回模型预测的答案字母（A/B/C/D），失败返回 '?'。"""
    opts_text = "\n".join(f"{k}. {v}" for k, v in options.items())
    user_msg = f"{question}\n\n选项：\n{opts_text}"

    try:
        kwargs = dict(
            model=model_cfg.model_id,
            messages=[
                {"role": "system", "content": MEDICAL_MC_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=model_cfg.temperature,
            max_tokens=5,
        )
        if model_cfg.extra_body:
            kwargs["extra_body"] = model_cfg.extra_body
        resp = await model_cfg.client.chat.completions.create(**kwargs)
        raw = resp.choices[0].message.content.strip().upper()
        # 提取第一个合法字母
        for ch in raw:
            if ch in options:
                return ch
        return raw[:1] if raw and raw[:1] in options else "?"
    except Exception as e:
        print(f"  [{model_cfg.name}] API 异常: {type(e).__name__}")
        return "?"


# ============================================================
# 评测主循环
# ============================================================

@dataclass
class ModelEvalResult:
    model_key: str
    model_name: str
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    error_set: set = field(default_factory=set)
    avg_latency: float = 0.0
    # 多选指标（全局聚合）
    total_tp: int = 0
    total_fp: int = 0
    total_fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0


def _score_multi_choice(pred: str, gt: str) -> Tuple[int, int, int]:
    """多选题评分：返回 (TP, FP, FN)。"""
    # 解析预测: "A,C,D" 或 "ACD" → set
    pred_set = set()
    for ch in pred.upper():
        if ch in "ABCDEFGHIJ":
            pred_set.add(ch)
    if not pred_set and pred.strip():
        pred_set = {pred.strip().upper()}

    # 解析答案: "ACD" 或 "A,C,D" → set
    gt_clean = gt.upper().replace(',', '').replace('，', '').replace(' ', '')
    gt_set = set()
    for ch in gt_clean:
        if ch in "ABCDEFGHIJ":
            gt_set.add(ch)

    tp = len(pred_set & gt_set)
    fp = len(pred_set - gt_set)
    fn = len(gt_set - pred_set)
    return tp, fp, fn


def _is_multi_choice(qt: str) -> bool:
    return '多选' in str(qt)


def _score_single_choice(pred: str, gt: str) -> bool:
    """单选题评分：精确匹配。"""
    p = pred.strip().upper()
    g = gt.strip().upper()
    return p == g


async def evaluate_model(
    model_key: str,
    model_cfg: ModelConfig,
    questions: List[dict],
    sem: asyncio.Semaphore,
) -> ModelEvalResult:
    """对单个模型跑全量评测。"""
    result = ModelEvalResult(model_key=model_key, model_name=model_cfg.name, total=len(questions))

    async def process_one(q: dict) -> Tuple[str, str, str, float]:
        async with sem:
            t0 = time.time()
            pred = await predict_one(model_cfg, q["question"], q["options"])
            lat = time.time() - t0
            return q["id"], pred, q["answer"], q.get("question_type", ""), lat

    tasks = [process_one(q) for q in questions]
    latencies = []
    for coro in asyncio.as_completed(tasks):
        qid, pred, gt, qtype, lat = await coro
        latencies.append(lat)
        if _is_multi_choice(qtype):
            tp, fp, fn = _score_multi_choice(pred, gt)
            result.total_tp += tp
            result.total_fp += fp
            result.total_fn += fn
            # 多选 count 每道题算 1 条 correct if 全对
            if fp == 0 and fn == 0:
                result.correct += 1
            else:
                result.error_set.add(qid)
        else:
            correct = _score_single_choice(pred, gt)
            if correct:
                result.correct += 1
                result.total_tp += 1
            else:
                result.error_set.add(qid)
                result.total_fp += 1
                result.total_fn += 1

    result.accuracy = round(result.correct / result.total, 4) if result.total else 0.0
    result.avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    # 计算全局 P/R/F1
    tp_fp = result.total_tp + result.total_fp
    tp_fn = result.total_tp + result.total_fn
    result.precision = round(result.total_tp / tp_fp, 4) if tp_fp > 0 else 0.0
    result.recall = round(result.total_tp / tp_fn, 4) if tp_fn > 0 else 0.0
    if result.precision + result.recall > 0:
        result.f1 = round(2 * result.precision * result.recall / (result.precision + result.recall), 4)
    return result


# ============================================================
# 错误重叠分析
# ============================================================

def compute_overlap_matrix(results: Dict[str, ModelEvalResult]) -> Dict[str, Any]:
    """
    计算模型两两之间的错误重叠率。
    overlap(A,B) = |error_A ∩ error_B| / min(|error_A|, |error_B|)
    重叠率越低 → 互补性越强 → 越适合辩论。
    """
    models = list(results.keys())
    matrix = {}
    for i, m1 in enumerate(models):
        for j, m2 in enumerate(models):
            if i >= j:
                continue
            e1 = results[m1].error_set
            e2 = results[m2].error_set
            inter = len(e1 & e2)
            denom = min(len(e1), len(e2)) if min(len(e1), len(e2)) > 0 else 1
            overlap = round(inter / denom, 4)
            key = f"{min(m1,m2)}|{max(m1,m2)}"
            matrix[key] = {
                "overlap": overlap,
                "shared_errors": inter,
                "m1_errors": len(e1),
                "m2_errors": len(e2),
                "diversity_score": round(1.0 - overlap, 4),
            }
    return matrix


def recommend_pair(
    results: Dict[str, ModelEvalResult],
    overlap_matrix: Dict[str, Any],
) -> List[Tuple[str, str, float, float]]:
    """
    推荐辩论组合，按 (平均准确率 × 多样性分数) 排序。
    得分越高 = 又准又互补。
    """
    scores = []
    models = list(results.keys())
    for i, m1 in enumerate(models):
        for j, m2 in enumerate(models):
            if i >= j:
                continue
            key = f"{min(m1,m2)}|{max(m1,m2)}"
            info = overlap_matrix.get(key, {})
            avg_acc = (results[m1].accuracy + results[m2].accuracy) / 2
            diversity = info.get("diversity_score", 0.0)
            combined = avg_acc * (1.0 + diversity)  # 多样性加权
            scores.append((m1, m2, avg_acc, diversity, combined))
    scores.sort(key=lambda x: x[4], reverse=True)
    return [(s[0], s[1], s[2], s[3]) for s in scores]


# ============================================================
# 打印报告
# ============================================================

def print_report(results: Dict[str, ModelEvalResult], overlap: Dict[str, Any]):
    print("\n" + "=" * 70)
    print("  基线模型评测报告")
    print("=" * 70)

    # 准确率排名
    print("\n【1. 单模型评测结果】")
    print(f"{'模型':<20} {'准确率':>8} {'精确率':>8} {'召回率':>8} {'F1':>8} {'正确':>6} {'总数':>6} {'均延迟':>8}")
    print("-" * 80)
    sorted_models = sorted(results.values(), key=lambda r: r.accuracy, reverse=True)
    for r in sorted_models:
        print(f"{r.model_name:<20} {r.accuracy:>7.1%} {r.precision:>7.1%} {r.recall:>7.1%} {r.f1:>7.1%} "
              f"{r.correct:>6} {r.total:>6} {r.avg_latency:>7.2f}s")

    # 错误重叠矩阵
    print("\n【2. 错误重叠矩阵】（越低越互补）")
    models = sorted(results.keys())
    header = " " * 16 + "".join(f"{results[m].model_name:<12}" for m in models)
    print(header)
    for m1 in models:
        row = f"{results[m1].model_name:<16}"
        for m2 in models:
            if m1 == m2:
                row += f"{'──':<12}"
            else:
                key = f"{min(m1,m2)}|{max(m1,m2)}"
                info = overlap.get(key, {})
                row += f"{info.get('overlap',0):<12.1%}"
        print(row)

    # 最佳辩论组合推荐
    print("\n【3. 最佳辩论组合推荐】")
    pairs = recommend_pair(results, overlap)
    for i, (m1, m2, avg_acc, div) in enumerate(pairs):
        combo = avg_acc * (1.0 + div)
        tag = " ← 推荐" if i == 0 else ""
        print(f"  {i+1}. {results[m1].model_name} + {results[m2].model_name}")
        print(f"     平均准确率: {avg_acc:.1%} | 多样性: {div:.1%} | 综合: {combo:.4f}{tag}")

    # 错误分布细节
    print("\n【4. 错误分布详情】")
    for m1 in models:
        for m2 in models:
            if m1 >= m2:
                continue
            key = f"{m1}|{m2}"
            info = overlap.get(key, {})
            print(f"  {results[m1].model_name} vs {results[m2].model_name}: "
                  f"共同错误 {info.get('shared_errors',0)} 题, "
                  f"独有错误 {info.get('m1_errors',0)-info.get('shared_errors',0)} / "
                  f"{info.get('m2_errors',0)-info.get('shared_errors',0)} 题")

    print("\n" + "=" * 70)


# ============================================================
# Main
# ============================================================

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="deepseek,qwen,glm",
                       help="候选模型列表（逗号分隔）")
    parser.add_argument("--subset", default="执业医师",
                       help="CMB 子类别")
    parser.add_argument("--limit", type=int, default=100,
                       help="评测题数上限（0=全部，默认 100）")
    parser.add_argument("--concurrency", type=int, default=3,
                       help="并发请求数")
    args = parser.parse_args()

    model_keys = [m.strip() for m in args.models.split(",") if m.strip() in MODELS]
    if not model_keys:
        print(f"错误：无有效模型。可用: {list(MODELS.keys())}")
        return

    print(f"候选模型: {[MODELS[k].name for k in model_keys]}")
    print(f"CMB 子集: {args.subset} | 题数上限: {args.limit} | 并发: {args.concurrency}")

    # 加载数据
    questions = await load_cmb_exam(subset=args.subset, max_q=args.limit)
    if not questions:
        print("无可评测数据，退出")
        return
    print(f"评测题数: {len(questions)}")

    # 逐模型评测
    sem = asyncio.Semaphore(args.concurrency)
    results: Dict[str, ModelEvalResult] = {}
    for mk in model_keys:
        cfg = MODELS[mk]
        print(f"\n{'='*50}")
        print(f"正在评测: {cfg.name} ({cfg.model_id})")
        print(f"{'='*50}")
        r = await evaluate_model(mk, cfg, questions, sem)
        results[mk] = r
        print(f"  → 准确率: {r.accuracy:.1%} ({r.correct}/{r.total}) "
              f"延迟: {r.avg_latency:.2f}s")

    # 错误重叠分析
    overlap = compute_overlap_matrix(results)

    # 生成报告
    print_report(results, overlap)

    # 保存结果（合并已有数据）
    out_dir = os.path.join(BASE_DIR, "experiments", "results", "baseline")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "baseline_results.json")

    # 加载已有结果
    existing = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    # 合并 models
    merged_models = existing.get("models", {})
    for mk, r in results.items():
        merged_models[mk] = {
            "name": r.model_name,
            "accuracy": r.accuracy,
            "precision": r.precision,
            "recall": r.recall,
            "f1": r.f1,
            "correct": r.correct,
            "total": r.total,
            "avg_latency": r.avg_latency,
            "errors": sorted(list(r.error_set))[:20],
        }

    out = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "models": merged_models,
        "overlap": overlap,
        "recommendations": [
            {"model1": m1, "model2": m2, "avg_accuracy": avg, "diversity": div}
            for m1, m2, avg, div in recommend_pair(results, overlap)
        ]
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
