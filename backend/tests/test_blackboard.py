"""
Blackboard smoke test（不依赖 pytest，直接 python -m backend.tests.test_blackboard 即可跑）
覆盖：
1. 基础 append + version 自增
2. 并发 append 不丢数据 + version 单调
3. snapshot 隔离性（快照不受后续写入影响）
4. latest / all_by_key / by_agent 读取路径
5. parent_refs → to_trace_dag() DAG 形态正确
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.core.blackboard import Blackboard


async def test_basic_append():
    bb = Blackboard(session_id="s1")
    v1 = await bb.append("symptoms", [{"name": "头痛"}], agent_id="perception")
    v2 = await bb.append("candidate_dx", [{"disease": "偏头痛"}], agent_id="proposer", parent_refs=[v1])
    assert v1 == 1 and v2 == 2
    assert bb.version == 2
    latest = bb.latest("candidate_dx")
    assert latest["value"][0]["disease"] == "偏头痛"
    assert latest["parents"] == [v1]
    print("[PASS] test_basic_append")


async def test_concurrent_append_no_loss():
    bb = Blackboard()

    async def writer(i):
        await bb.append("tick", i, agent_id=f"w{i}")

    await asyncio.gather(*[writer(i) for i in range(100)])
    assert bb.version == 100
    entries = bb.all_by_key("tick")
    assert len(entries) == 100
    versions = [e["v"] for e in entries]
    assert versions == sorted(versions), "version 应单调递增"
    assert set(versions) == set(range(1, 101)), "version 应连续不丢"
    print("[PASS] test_concurrent_append_no_loss")


async def test_snapshot_isolation():
    bb = Blackboard()
    await bb.append("k", "v1", agent_id="a")
    snap = bb.snapshot()
    await bb.append("k", "v2", agent_id="a")
    # 快照里应该只看到 v1
    assert len(snap) == 1
    assert bb.latest("k", snap=snap)["value"] == "v1"
    # 实时状态应该看到 v2
    assert bb.latest("k")["value"] == "v2"
    print("[PASS] test_snapshot_isolation")


async def test_by_agent_filter():
    bb = Blackboard()
    await bb.append("x", 1, agent_id="proposer")
    await bb.append("x", 2, agent_id="critic")
    await bb.append("x", 3, agent_id="proposer")
    assert len(bb.by_agent("proposer")) == 2
    assert len(bb.by_agent("critic")) == 1
    print("[PASS] test_by_agent_filter")


async def test_trace_dag():
    bb = Blackboard()
    v1 = await bb.append("symptoms", "headache", agent_id="perception")
    v2 = await bb.append("candidate_dx", "migraine", agent_id="proposer", parent_refs=[v1])
    v3 = await bb.append("objections", "check BP", agent_id="critic", parent_refs=[v2])
    v4 = await bb.append("candidate_dx", "tension headache", agent_id="defender", parent_refs=[v2, v3])

    dag = bb.to_trace_dag()
    assert len(dag["nodes"]) == 4
    assert len(dag["edges"]) == 4  # v1->v2, v2->v3, v2->v4, v3->v4
    edge_set = {(e["from"], e["to"]) for e in dag["edges"]}
    assert (v1, v2) in edge_set
    assert (v2, v3) in edge_set
    assert (v2, v4) in edge_set
    assert (v3, v4) in edge_set
    print("[PASS] test_trace_dag")


async def main():
    await test_basic_append()
    await test_concurrent_append_no_loss()
    await test_snapshot_isolation()
    await test_by_agent_filter()
    await test_trace_dag()
    print("\n[OK] Blackboard smoke tests all passed.")


if __name__ == "__main__":
    asyncio.run(main())
