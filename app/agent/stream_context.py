"""本轮已校验文本的流式通道，图与响应节点共用，独立于图实现。"""

from contextvars import ContextVar
from asyncio import Queue

stream_event_queue: ContextVar[Queue | None] = ContextVar("agent_stream_event_queue", default=None)
