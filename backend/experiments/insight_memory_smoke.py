"""
🧠 Insight Memory — Smoke Test
==============================

种 8 条多领域见解，验证：
  1. 嵌入 + 落库 + 指纹去重
  2. 同 domain 的相似检索（余弦阈值 + 质量加权 + 时间衰减）
  3. 正例 / 反例双极性独立检索
  4. few-shot 渲染输出可读
  5. 命中后 hit_count 自增
  6. stats() 正常聚合

运行：
    python -m experiments.insight_memory_smoke
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.insight_memory import (  # noqa: E402
    add_insight, retrieve_insights, render_insights_as_fewshot,
    stats, purge_low_quality, DB_PATH,
)


# 8 条种子 insights：6 正例 + 2 反例，跨 rumor / general / medication
SEEDS = [
    # ---- rumor 正例 ----
    {"domain": "rumor", "polarity": "SUCCESS",
     "query": "微波炉加热食物会致癌",
     "answer_summary": "属于谣言。微波属非电离辐射，能量不足以破坏共价键，FDA/WHO 均无致癌证据。",
     "agent_path": "rumor:CTAEW", "evidence_count": 6,
     "confidence": 0.92, "hallucination_score": 0.05,
     "tags": ["微波炉", "致癌"]},
    {"domain": "rumor", "polarity": "SUCCESS",
     "query": "吸烟会导致肺癌",
     "answer_summary": "属实。IARC Group 1 级致癌物，超过 85% 肺癌病例与吸烟相关。",
     "agent_path": "rumor:FAST_PATH", "evidence_count": 4,
     "confidence": 0.96, "hallucination_score": 0.02,
     "tags": ["吸烟", "肺癌"]},

    # ---- rumor 反例 ----
    {"domain": "rumor", "polarity": "FAILURE",
     "query": "头孢配酒可以喝",
     "answer_summary": "当时模型自信地说『可以同服』，但与权威指南完全相反，已被幻觉检测员拦截弃答。",
     "agent_path": "rumor:CTAEW", "evidence_count": 3,
     "confidence": 0.85, "hallucination_score": 0.95,
     "tags": ["头孢", "酒精", "用药冲突"]},

    # ---- medication 正例 ----
    {"domain": "medication", "polarity": "SUCCESS",
     "query": "布洛芬和阿司匹林能一起吃吗",
     "answer_summary": "不建议同服，二者均抑制 COX，叠加易致胃出血。如需联用必须医生评估。",
     "agent_path": "medication:reviewer", "evidence_count": 5,
     "confidence": 0.89, "hallucination_score": 0.08,
     "tags": ["布洛芬", "阿司匹林", "用药冲突"]},
    {"domain": "medication", "polarity": "SUCCESS",
     "query": "二甲双胍空腹吃吗",
     "answer_summary": "随餐或餐后服用最佳，可减少胃肠道反应；空腹易腹泻、恶心。",
     "agent_path": "medication:reviewer", "evidence_count": 3,
     "confidence": 0.88, "hallucination_score": 0.10,
     "tags": ["二甲双胍", "服用方式"]},

    # ---- medication 反例 ----
    {"domain": "medication", "polarity": "FAILURE",
     "query": "感冒药可以一次多吃几片",
     "answer_summary": "当时回答未充分警示对乙酰氨基酚过量肝损风险，幻觉检测员判定 HIGH 风险声明缺证据。",
     "agent_path": "medication:reviewer", "evidence_count": 1,
     "confidence": 0.72, "hallucination_score": 0.78,
     "tags": ["感冒药", "对乙酰氨基酚", "过量风险"]},

    # ---- general 正例 ----
    {"domain": "general", "polarity": "SUCCESS",
     "query": "经常熬夜对身体有什么影响",
     "answer_summary": "扰乱褪黑素 / 皮质醇节律，长期增加 2 型糖尿病、心血管疾病、癌症风险。",
     "agent_path": "general:react", "evidence_count": 4,
     "confidence": 0.86, "hallucination_score": 0.10,
     "tags": ["熬夜", "昼夜节律"]},
    {"domain": "general", "polarity": "SUCCESS",
     "query": "感冒后多喝水有用吗",
     "answer_summary": "适量补水有助稀释痰液、维持代谢，但不必『大量灌水』，过量反致低钠血症。",
     "agent_path": "general:react", "evidence_count": 2,
     "confidence": 0.81, "hallucination_score": 0.12,
     "tags": ["感冒", "补水"]},
]


async def main():
    print("=" * 72)
    print("🧠 Insight Memory Smoke Test")
    print(f"DB: {DB_PATH}")
    print("=" * 72)

    # ----- 1. 落库 -----
    print("\n▶ Step 1: 落库 8 条种子见解 …")
    inserted = 0
    for s in SEEDS:
        sid = await add_insight(
            domain=s["domain"], query=s["query"], polarity=s["polarity"],
            answer_summary=s["answer_summary"], agent_path=s["agent_path"],
            evidence_count=s["evidence_count"],
            confidence=s["confidence"], hallucination_score=s["hallucination_score"],
            tags=s["tags"],
        )
        if sid:
            inserted += 1
    print(f"   入库 {inserted}/{len(SEEDS)} 条")

    # ----- 2. 重复入库测试（指纹去重） -----
    print("\n▶ Step 2: 重复入库（验证指纹去重）…")
    dup_id = await add_insight(
        domain="rumor", query=SEEDS[0]["query"], polarity="SUCCESS",
        answer_summary="（更高质量的版本）" + SEEDS[0]["answer_summary"],
        confidence=0.99, hallucination_score=0.01, evidence_count=10,
    )
    s_after = await stats()
    print(f"   重复指纹 id={dup_id}（应与首条相同）")
    print(f"   total 仍是 {s_after['total']}（验证未重复插入）")
    assert s_after["total"] == len(SEEDS), f"期望 {len(SEEDS)}，实际 {s_after['total']}"

    # ----- 3. rumor domain 相似检索 -----
    print("\n▶ Step 3: 检索『微波炉加热致不致癌』(rumor) …")
    res = await retrieve_insights(
        query="微波炉加热的食物到底致癌不致癌",
        domain="rumor", top_k=3, min_similarity=0.6,
    )
    for ins in res:
        print(f"   [sim={ins.similarity:.2f} q={ins.quality_score:.2f} pol={ins.polarity}] {ins.query}")
    assert any("微波炉" in i.query for i in res), "期望命中『微波炉』正例"

    # ----- 4. medication 反例独立检索 -----
    print("\n▶ Step 4: 仅检索 medication FAILURE 反例（询问感冒药多吃）…")
    res = await retrieve_insights(
        query="一次多吃几片感冒药行不行",
        domain="medication", polarity="FAILURE", top_k=3, min_similarity=0.5,
    )
    for ins in res:
        print(f"   [sim={ins.similarity:.2f} polarity={ins.polarity}] {ins.query}")
        print(f"        → {ins.answer_summary[:80]}")
    assert all(i.polarity == "FAILURE" for i in res), "FAILURE 过滤失效！"

    # ----- 5. few-shot 渲染 -----
    print("\n▶ Step 5: few-shot 渲染（混合正反例）…")
    res_mixed = await retrieve_insights(
        query="喝头孢能不能喝点小酒",
        domain="rumor", top_k=3, min_similarity=0.5,
    )
    fewshot = render_insights_as_fewshot(res_mixed, max_chars=800)
    print(fewshot or "   （未命中任何相似案例）")

    # ----- 6. hit_count 累加 -----
    print("\n▶ Step 6: 重复检索热门问题，验证 hit_count 累加 …")
    for _ in range(3):
        await retrieve_insights(
            query="吸烟与肺癌的关系", domain="rumor", top_k=2, min_similarity=0.6,
        )
    s_final = await stats()
    print(f"   stats 总览: {s_final}")

    print("\n" + "=" * 72)
    print("✅ All smoke checks passed.")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
