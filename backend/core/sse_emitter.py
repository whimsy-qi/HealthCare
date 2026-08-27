"""
SSE 辩论事件广播器
==================
用 ContextVar 管理"当前请求的 SSE 队列"，让深层嵌套的 MADDx workflow 不必
沿调用链传 queue 参数，即可把辩论过程事件 push 到前端。

关键：ContextVar 在 asyncio.create_task 创建时会**复制父任务的上下文**，
所以只要在 create_task 之前 set_queue()，子任务里的 emit() 就能找到同一个 queue。
"""
import asyncio
import logging
from contextvars import ContextVar
from typing import Optional

logger = logging.getLogger("SSE")

_queue_var: ContextVar[Optional[asyncio.Queue]] = ContextVar("sse_queue", default=None)
_collector_var: ContextVar[Optional[list]] = ContextVar("sse_collector", default=None)


def set_queue(q: asyncio.Queue):
    return _queue_var.set(q)


def reset_queue(token):
    _queue_var.reset(token)


def set_collector(lst: list):
    """可选：设置一个收集列表，emit 会同时把事件追加进来，用于历史回放落库。"""
    return _collector_var.set(lst)


def reset_collector(token):
    _collector_var.reset(token)


async def emit(event_type: str, **data):
    """
    从任意深度的异步代码调用：
      - 若 queue 已设置，事件 push 到 SSE 队列（前端实时可见）
      - 若 collector 已设置，事件同时 append 到列表（用于落库历史回放）
    非 SSE 上下文下为无害 no-op。
    """
    payload = {"type": event_type, **data}
    collector = _collector_var.get()
    if collector is not None:
        collector.append(payload)
    q = _queue_var.get()
    if q is None:
        return
    try:
        await q.put(payload)
    except Exception as e:
        logger.warning(f"SSE emit 失败 ({event_type}): {e}")
