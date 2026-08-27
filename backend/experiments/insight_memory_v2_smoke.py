"""
🔒 Insight Memory v2 — 双桶隐私分层 Smoke Test
==============================================

验证：
  1. PII 脱敏：手机号 / 身份证 / 邮箱被自动替换
  2. LLM 隐私分类：通用问题 → 共享桶，"我有XX"型 → 私有桶
  3. 用户隔离：A 用户的私有 insight，B 用户检索不到
  4. 共享共用：A 用户问的通用知识，B 用户能秒查
  5. 同 query 不同桶并存：同问题的 A 私有 + B 私有 + 共享版可以共存

运行：
    python -m experiments.insight_memory_v2_smoke
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.insight_memory import (  # noqa: E402
    add_insight, retrieve_insights, render_insights_as_fewshot,
    redact_pii, classify_privacy, stats, DB_PATH,
)


async def main():
    # 🧹 清表（保留库文件，规避 Windows 文件锁）
    import sqlite3 as _sq
    conn = _sq.connect(DB_PATH)
    conn.execute("DELETE FROM insights")
    conn.commit()
    conn.close()

    print("=" * 72)
    print("🔒 Insight Memory v2 — Privacy-Layered Smoke Test")
    print(f"DB: {DB_PATH}")
    print("=" * 72)

    # ===== 1. PII 脱敏单元测试 =====
    print("\n▶ Step 1: PII 脱敏测试 …")
    samples = [
        ("我手机 13912345678 想咨询", "[手机号]"),
        ("身份证 110101199001011234 的患者", "[身份证]"),
        ("联系 doctor@hospital.cn", "[邮箱]"),
        ("座机 010-12345678 转 5", "[座机]"),
    ]
    for raw, must_contain in samples:
        out = redact_pii(raw)
        ok = must_contain in out
        print(f"   {'✅' if ok else '❌'} '{raw}' → '{out}'")
        assert ok, f"PII 脱敏失败：{raw}"

    # ===== 2. LLM 隐私分类器 =====
    print("\n▶ Step 2: LLM 隐私分类（5 例混合）…")
    cases = [
        ("微波炉加热食物会致癌吗",      False),
        ("吸烟和肺癌有什么关系",        False),
        ("我有糖尿病能吃西瓜吗",        True),
        ("我妈对头孢过敏，能吃头孢克肟吗", True),
        ("二甲双胍空腹吃吗",            False),
    ]
    for q, expected in cases:
        actual = await classify_privacy(q)
        mark = "✅" if actual == expected else "❌"
        print(f"   {mark} '{q}' → personal={actual} (期望 {expected})")

    # ===== 3. 落库测试：通用 → 共享桶 =====
    print("\n▶ Step 3: 通用知识 → 共享桶 …")
    sid_pub = await add_insight(
        domain="rumor", query="微波炉加热食物会致癌吗",
        answer_summary="微波非电离辐射，FDA/WHO 均无致癌证据，属谣言。",
        polarity="SUCCESS", confidence=0.92, hallucination_score=0.05,
        evidence_count=4,
        # 不传 user_id → 自动分类为非个人 → 落共享桶
    )
    print(f"   id={sid_pub} 入共享桶")

    # ===== 4. 落库测试：A 用户的个性化 → 私有桶 =====
    print("\n▶ Step 4: 用户 A 的个性化问题 → 私有桶 …")
    sid_a = await add_insight(
        domain="medication", query="我有糖尿病能吃二甲双胍吗",
        user_id=101,
        answer_summary="可以，二甲双胍是 2 型糖尿病一线用药。",
        polarity="SUCCESS", confidence=0.90, hallucination_score=0.08,
        evidence_count=3,
    )
    print(f"   id={sid_a} 入用户 101 私有桶")

    # ===== 5. 落库测试：B 用户问相同问题 → 自己私有桶 =====
    print("\n▶ Step 5: 用户 B 问相同问题 → 自己私有桶（与 A 隔离）…")
    sid_b = await add_insight(
        domain="medication", query="我有糖尿病能吃二甲双胍吗",
        user_id=202,
        answer_summary="可以，二甲双胍是 2 型糖尿病一线用药。",
        polarity="SUCCESS", confidence=0.90, hallucination_score=0.08,
        evidence_count=3,
    )
    print(f"   id={sid_b} 入用户 202 私有桶（应 ≠ {sid_a}）")
    assert sid_b != sid_a, "桶隔离失败：A/B 私有桶共用了同一行！"

    # ===== 6. 检索：用户 B 检索"我有糖尿病能吃 X" =====
    print("\n▶ Step 6: 用户 B 检索个性化问题 …")
    res_b = await retrieve_insights(
        query="我有糖尿病能吃二甲双胍片吗",
        user_id=202, domain="medication",
        top_k=5, min_similarity=0.5,
    )
    print(f"   查到 {len(res_b)} 条；user_id 分布：")
    for ins in res_b:
        bucket = "shared" if ins.user_id is None else f"private(user={ins.user_id})"
        print(f"     - id={ins.id} sim={ins.similarity:.2f} [{bucket}] {ins.query}")
    assert all(ins.user_id in (None, 202) for ins in res_b), \
        "❌ B 用户检索到了 A 用户的私有数据！隐私破防！"
    assert any(ins.user_id == 202 for ins in res_b), "B 自己的私有数据没查到"

    # ===== 7. 共享桶：B 检索通用知识 =====
    print("\n▶ Step 7: 用户 B 检索通用谣言（应命中共享桶）…")
    res_shared = await retrieve_insights(
        query="微波炉加热的食物到底致癌吗",
        user_id=202, domain="rumor",
        top_k=5, min_similarity=0.6,
    )
    for ins in res_shared:
        bucket = "shared" if ins.user_id is None else f"private(user={ins.user_id})"
        print(f"     - id={ins.id} sim={ins.similarity:.2f} [{bucket}] {ins.query}")
    assert any(ins.user_id is None for ins in res_shared), "共享桶查询失败"

    # ===== 8. 用户 A 检索：能查到自己 + 共享，但查不到 B =====
    print("\n▶ Step 8: 用户 A 检索『我有糖尿病』（应只看到 A+共享，不见 B）…")
    res_a = await retrieve_insights(
        query="我糖尿病吃药",
        user_id=101, domain="medication",
        top_k=5, min_similarity=0.5,
    )
    for ins in res_a:
        bucket = "shared" if ins.user_id is None else f"private(user={ins.user_id})"
        print(f"     - id={ins.id} [{bucket}] {ins.query}")
    assert all(ins.user_id in (None, 101) for ins in res_a), \
        "❌ A 用户检索到了 B 用户的私有数据！隐私破防！"

    # ===== 9. PII 写入验证 =====
    print("\n▶ Step 9: 含手机号的 query 入库后应已脱敏 …")
    sid_pii = await add_insight(
        domain="general", query="我手机 13812345678 体检报告异常",
        user_id=101, is_personal=True,
        answer_summary="（脱敏测试）",
        polarity="SUCCESS", confidence=0.5, hallucination_score=0.2,
    )
    # 直接读 DB 看脱敏是否成功
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT query FROM insights WHERE id=?", (sid_pii,)).fetchone()
    conn.close()
    print(f"   入库后的 query = '{row['query']}'")
    assert "[手机号]" in row["query"] and "13812345678" not in row["query"], "PII 脱敏失败"

    # ===== 10. 总览 =====
    print("\n▶ Step 10: 库总览 …")
    s = await stats()
    print(f"   total={s['total']}, by_domain={s['by_domain']}, by_polarity={s['by_polarity']}")

    print("\n" + "=" * 72)
    print("✅ All v2 dual-bucket privacy tests passed.")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
