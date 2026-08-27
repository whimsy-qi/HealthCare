"""
MADDx Blackboard: Append-only 线程安全黑板
----------------------------------------------------
设计要点（对应论文 4.2 节）：
1. **Append-only**：禁止 update/delete，所有历史保留，天然可追溯
2. **Version 单调递增**：每条 entry 带唯一 version id，支持 based_on_version 追溯
3. **Parent refs**：DAG 式溯源，渲染 trace graph 时直接用
4. **Snapshot isolation**：Agent 执行期间读到不可变快照，避免中途状态漂移
5. **asyncio.Lock 串行化写入**：单进程内够用，无需外部队列
"""
import time
import asyncio
from typing import Any, List, Optional, Dict, Tuple


class BlackboardEntry(dict):
    """一条黑板记录。继承 dict 方便 JSON 序列化。"""
    def __init__(self, version: int, key: str, value: Any,
                 agent_id: str, parent_refs: List[int]):
        super().__init__(
            v=version,
            key=key,
            value=value,
            agent=agent_id,
            ts=time.time(),
            parents=parent_refs or [],
        )


class Blackboard:
    """
    单一会话的黑板实例。每次用户发起一次诊断咨询应当 new 一个新的 Blackboard，
    避免跨会话污染。上层会把它挂在 LangGraph 的 AgentState 里透传。
    """

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self._log: List[BlackboardEntry] = []
        self._version: int = 0
        self._lock = asyncio.Lock()

    # ---------- 写入 ----------

    async def append(
        self,
        key: str,
        value: Any,
        agent_id: str,
        parent_refs: Optional[List[int]] = None,
    ) -> int:
        """
        追加一条记录，返回新的 version id。
        parent_refs: 本条记录所依据的父条目 version 列表，用于构建 trace DAG。
        """
        async with self._lock:
            self._version += 1
            entry = BlackboardEntry(
                version=self._version,
                key=key,
                value=value,
                agent_id=agent_id,
                parent_refs=parent_refs or [],
            )
            self._log.append(entry)
            return self._version

    # ---------- 读取 ----------

    def snapshot(self) -> Tuple[BlackboardEntry, ...]:
        """返回不可变快照，Agent 执行期间应当固定使用它，避免读到中途写入。"""
        return tuple(self._log)

    def latest(self, key: str, snap: Optional[Tuple[BlackboardEntry, ...]] = None) -> Optional[BlackboardEntry]:
        """取某个 key 的最新一条记录。"""
        log = snap if snap is not None else self._log
        for entry in reversed(log):
            if entry["key"] == key:
                return entry
        return None

    def all_by_key(self, key: str, snap: Optional[Tuple[BlackboardEntry, ...]] = None) -> List[BlackboardEntry]:
        """取某个 key 的全部历史记录（按时间顺序）。"""
        log = snap if snap is not None else self._log
        return [e for e in log if e["key"] == key]

    def by_agent(self, agent_id: str) -> List[BlackboardEntry]:
        return [e for e in self._log if e["agent"] == agent_id]

    def count_since(self, key: str, since_version: int) -> int:
        """
        统计 since_version（不含）之后写入的、指定 key 的条目数。
        D8: 用于 Rule 4 NO_NEW_EVIDENCE 终止判据——
        若本轮 tool_result 计数为 0，说明 agent 已无力取得新证据。
        """
        return sum(1 for e in self._log if e["key"] == key and e["v"] > since_version)

    def filter(self, key: Optional[str] = None, agent_id: Optional[str] = None) -> List[BlackboardEntry]:
        """D8: 多条件组合过滤，辅助前端 DAG 渲染与调试。"""
        return [
            e for e in self._log
            if (key is None or e["key"] == key)
            and (agent_id is None or e["agent"] == agent_id)
        ]

    @property
    def version(self) -> int:
        return self._version

    # ---------- 可视化与调试 ----------

    def to_trace_dag(self) -> Dict[str, Any]:
        """
        导出为 trace DAG，格式：
        { "nodes": [{"id": v, "label": key, "agent": ..., "value_preview": ...}],
          "edges": [{"from": parent_v, "to": child_v}] }
        供前端渲染辩论过程时序图使用（D7 演示素材）。
        """
        nodes = []
        edges = []
        for e in self._log:
            preview = str(e["value"])
            if len(preview) > 80:
                preview = preview[:77] + "..."
            nodes.append({
                "id": e["v"],
                "label": e["key"],
                "agent": e["agent"],
                "ts": e["ts"],
                "preview": preview,
                "value": e["value"],  # D7：完整结构化数据，供前端辩论卡片渲染
            })
            for p in e["parents"]:
                edges.append({"from": p, "to": e["v"]})
        return {"nodes": nodes, "edges": edges}

    def dump(self) -> List[dict]:
        """完整 log 序列化，调试用。"""
        return [dict(e) for e in self._log]
