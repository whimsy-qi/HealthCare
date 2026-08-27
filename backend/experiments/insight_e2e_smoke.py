"""
🧠 Insight Memory — End-to-End Smoke
====================================

模拟两次完整对话流（不经 HTTP，直接调 graph）：

  1. 用户 101 问"微波炉加热食物会致癌吗"（rumor，非个人）
     → 预期：rumor 子图跑完 → halluc guard → harvest 入【共享桶】（user_id=NULL）

  2. 用户 202 问"我有糖尿病能不能吃二甲双胍"（general/medication，个人）
     → 预期：相应 agent 跑完 → harvest 入【私有桶】（user_id=202）

最后查 DB 验证：
  - 共享桶有 1 条 rumor 记录，user_id=NULL
  - 私有桶有 1 条 user_id=202 的记录

运行：
    python -m experiments.insight_e2e_smoke
"""
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.insight_memory import DB_PATH  # noqa: E402


async def main():
    # 清表
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM insights")
    conn.commit()
    conn.close()

    # 用 graph_engine 跑实际节点
    from graph_engine import app_graph

    print("=" * 70)
    print("🧠 Insight E2E Smoke — Full Graph Path")
    print("=" * 70)

    # ----- Case 1: 用户 101 问 rumor 通用问题 -----
    print("\n▶ Case 1: 用户 101 问『微波炉加热食物会致癌吗』(rumor) …")
    init_state_1 = {
        "session_id": -1, "user_id": 101,
        "query": "微波炉加热食物会致癌吗",
        "messages_history": [], "image_url": None,
        "patient_profile": {}, "extracted_entities": [],
        "trace_data": {}, "agent_audit_log": [], "internal_scratchpad": [],
        "is_finished": False, "turn_count": 0, "current_slots": {}, "current_route": "",
        "vision_context": None, "med_precheck_result": None, "options": [],
        "response_images": [], "final_answer": "",
    }
    out1 = await app_graph.ainvoke(init_state_1)
    print(f"   final_answer (前 80 字): {(out1.get('final_answer') or '')[:80]}…")
    print(f"   route: {out1.get('current_route')}")
    halluc_1 = (out1.get("trace_data") or {}).get("hallucination_check") or {}
    print(f"   halluc action: {halluc_1.get('action')} score={halluc_1.get('hallucination_score')}")

    # 等 fire-and-forget 收割完成（最多 8s）
    print("   ⏳ 等待 fire-and-forget harvest …")
    await asyncio.sleep(8)

    # ----- Case 2: 用户 202 问个性化用药问题 -----
    print("\n▶ Case 2: 用户 202 问『我有糖尿病能吃二甲双胍吗』(personal) …")
    init_state_2 = {
        "session_id": -2, "user_id": 202,
        "query": "我有糖尿病能不能吃二甲双胍",
        "messages_history": [], "image_url": None,
        "patient_profile": {"diseases": ["2 型糖尿病"]},
        "extracted_entities": [], "trace_data": {}, "agent_audit_log": [],
        "internal_scratchpad": [], "is_finished": False, "turn_count": 0,
        "current_slots": {}, "current_route": "", "vision_context": None,
        "med_precheck_result": None, "options": [], "response_images": [],
        "final_answer": "",
    }
    out2 = await app_graph.ainvoke(init_state_2)
    print(f"   final_answer (前 80 字): {(out2.get('final_answer') or '')[:80]}…")
    print(f"   route: {out2.get('current_route')}")
    halluc_2 = (out2.get("trace_data") or {}).get("hallucination_check") or {}
    print(f"   halluc action: {halluc_2.get('action')} score={halluc_2.get('hallucination_score')}")

    # 等 fire-and-forget 收割完成
    print("   ⏳ 等待 fire-and-forget harvest …")
    await asyncio.sleep(8)

    # ----- 验证 DB -----
    print("\n▶ 验证 SQLite 落库情况 …")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, domain, user_id, is_personal, polarity, "
        "quality_score, hallucination_score, query, agent_path "
        "FROM insights ORDER BY id"
    ).fetchall()
    conn.close()

    if not rows:
        print("   ❌ 库里一条记录都没有 — fire-and-forget 可能被吞了")
        return
    print(f"\n   库里共 {len(rows)} 条记录：")
    for r in rows:
        bucket = "shared" if r["user_id"] is None else f"private(user={r['user_id']})"
        print(
            f"   - id={r['id']} domain={r['domain']} bucket={bucket} "
            f"polarity={r['polarity']} q={r['quality_score']} "
            f"halluc={r['hallucination_score']:.2f} | {r['query'][:40]}"
        )
        print(f"       agent_path: {r['agent_path']}")

    # 断言：至少 case 1 进了共享桶
    has_shared = any(r["user_id"] is None for r in rows)
    has_private_202 = any(r["user_id"] == 202 for r in rows)
    print()
    print(f"   {'✅' if has_shared else '⚠️ '} 共享桶（user_id=NULL）: {'有' if has_shared else '无'}")
    print(f"   {'✅' if has_private_202 else '⚠️ '} 用户 202 私有桶: {'有' if has_private_202 else '无'}")

    print("\n" + "=" * 70)
    print("✅ E2E smoke complete.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
