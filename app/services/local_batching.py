"""单进程候选：有界跨请求向量批处理及独立重排通道，无共享结果缓存。"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
import threading
import time
import uuid
from typing import Any, Callable

from app.services.local_inference import CONTEXT, LocalModelBusy, LocalModelDeadline


@dataclass(eq=False)
class _Ticket:
    texts: tuple[str, ...]
    deadline: float
    guard: Callable[[], None]
    priority: str
    events: list | None
    began: float = field(default_factory=time.monotonic)
    dispatched: bool = False
    done: bool = False
    result: Any = None
    error: Exception | None = None


class EmbeddingBatcher:
    """调用方执行批次，无线程池积压；每实例固定同一provider/模型/维度。"""

    def __init__(self, infer: Callable, *, max_requests: int = 10,
                 max_items: int = 20, window_seconds: float = .003):
        if max_requests < 1 or max_items < 1 or not 0 <= window_seconds <= .01:
            raise ValueError("invalid_embedding_batch_limits")
        self.infer = infer
        self.max_requests = max_requests
        self.max_items = max_items
        self.window_seconds = window_seconds
        self._condition = threading.Condition()
        self._queue: list[_Ticket] = []
        self._outstanding = 0
        self._running = False
        self._closed = False
        self._interactive_streak = 0

    def close(self) -> None:
        with self._condition:
            self._closed = True
            for ticket in self._queue:
                ticket.error = LocalModelDeadline("embedding_batcher_closed")
                ticket.done = True
            self._queue.clear()
            self._condition.notify_all()

    def submit(self, texts: tuple[str, ...], *, deadline: float,
               guard: Callable[[], None]) -> list:
        if not texts or len(texts) > self.max_items:
            raise ValueError("embedding_batch_item_limit")
        context = CONTEXT.get()
        ticket = _Ticket(texts, deadline, guard, context.priority, context.events)
        with self._condition:
            if self._closed:
                raise LocalModelDeadline("embedding_batcher_closed")
            guard()
            if time.monotonic() >= deadline:
                raise LocalModelDeadline("embedding_batch_expired")
            if self._outstanding >= self.max_requests:
                raise LocalModelBusy("embedding_batch_capacity")
            self._outstanding += 1
            self._queue.append(ticket)
            self._condition.notify_all()
        try:
            while True:
                lead = False
                with self._condition:
                    if ticket.done:
                        if ticket.error is not None:
                            raise ticket.error
                        return ticket.result
                    # 在途HTTP必须归还后才允许上层释放整轮资格。
                    if not ticket.dispatched:
                        try:
                            guard()
                            if time.monotonic() >= deadline:
                                raise LocalModelDeadline("embedding_batch_queue_deadline")
                        except Exception:
                            self._queue.remove(ticket)
                            self._condition.notify_all()
                            raise
                    if not self._running and self._queue:
                        self._running = True
                        lead = True
                    else:
                        self._condition.wait(.01 if ticket.dispatched else max(.0001, min(.01, deadline-time.monotonic())))
                if lead:
                    self._execute()
        finally:
            with self._condition:
                self._outstanding -= 1
                self._condition.notify_all()

    def _execute(self) -> None:
        batch: list[_Ticket] = []
        dispatched_at = None
        batch_id = uuid.uuid4().hex
        try:
            with self._condition:
                until = min(time.monotonic()+self.window_seconds,
                            min(t.deadline for t in self._queue))
                while not self._closed and time.monotonic() < until:
                    if sum(len(t.texts) for t in self._queue) >= self.max_items:
                        break
                    self._condition.wait(until-time.monotonic())
                # 按FIFO兼容打包；连续四批前台后优先一个后台，防止索引饥饿。
                background_first = self._interactive_streak >= 4
                ordered = sorted(self._queue, key=lambda t: t.priority != (
                    "background" if background_first else "interactive"))
                count = 0
                for ticket in ordered:
                    try:
                        ticket.guard()
                        if self._closed or time.monotonic() >= ticket.deadline:
                            raise LocalModelDeadline("embedding_batch_expired_before_dispatch")
                    except Exception as exc:
                        ticket.error, ticket.done = exc, True
                        self._queue.remove(ticket)
                        continue
                    if count + len(ticket.texts) > self.max_items:
                        continue
                    self._queue.remove(ticket)
                    ticket.dispatched = True
                    batch.append(ticket)
                    count += len(ticket.texts)
                if not batch:
                    return
                self._interactive_streak = (self._interactive_streak+1 if all(
                    t.priority == "interactive" for t in batch) else 0)
            dispatched_at = time.monotonic()
            def guard():
                # lane可在另一provider实例后等待，HTTP派发前再次核对所有成员。
                for ticket in batch:
                    ticket.guard()
                    if time.monotonic() >= ticket.deadline:
                        raise LocalModelDeadline("embedding_batch_expired_before_http")

            values = self.infer([text for t in batch for text in t.texts],
                                min(t.deadline for t in batch), guard)
            if not isinstance(values, list) or len(values) != count:
                raise ValueError("embedding_batch_response_count")
            offset = 0
            for ticket in batch:
                try:
                    ticket.guard()
                    if time.monotonic() >= ticket.deadline:
                        raise LocalModelDeadline("late_embedding_batch_result")
                    ticket.result = values[offset:offset+len(ticket.texts)]
                except Exception as exc:
                    ticket.error = exc
                offset += len(ticket.texts)
        except Exception as exc:
            for ticket in batch:
                ticket.error = exc
        finally:
            with self._condition:
                for ticket in batch:
                    ticket.done = True
                    if ticket.events is not None:
                        ticket.events.append({"purpose": "embedding_batch", "requests": len(batch),
                            "batch_id": batch_id,
                            "items": sum(len(t.texts) for t in batch),
                            "status": "failed" if ticket.error else "succeeded",
                            "queue_ms": round(((dispatched_at or time.monotonic())-ticket.began)*1000, 3),
                            # 这是分组观测，不重复计作每个成员额外派发一次模型。
                            "dispatched": False, "inference_ms": None})
                self._running = False
                self._condition.notify_all()


class ModelLane:
    """固定容量；请求已获整轮资格后只受原deadline约束，不二次BUSY。"""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("invalid_model_lane_capacity")
        self.capacity = capacity
        self._condition = threading.Condition()
        self._active = 0
        self._queues = {"interactive": deque(), "background": deque()}
        self._interactive_streak = 0

    def run(self, call: Callable, remaining: float):
        deadline = time.monotonic()+remaining
        priority = CONTEXT.get().priority
        marker = object()
        with self._condition:
            queue = self._queues[priority]
            if sum(map(len, self._queues.values())) >= 10:
                raise LocalModelBusy("local_model_lane_capacity")
            queue.append(marker)
            self._condition.notify_all()
            try:
                while True:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise LocalModelDeadline("local_model_lane_deadline")
                    interactive, background = self._queues["interactive"], self._queues["background"]
                    first = (background if background and (not interactive or self._interactive_streak >= 4)
                             else interactive or background)
                    if self._active < self.capacity and first[0] is marker:
                        queue.remove(marker)
                        self._active += 1
                        self._interactive_streak = self._interactive_streak+1 if priority == "interactive" else 0
                        self._condition.notify_all()
                        break
                    self._condition.wait(remaining)
            finally:
                if marker in queue:
                    queue.remove(marker)
                    self._condition.notify_all()
        try:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise LocalModelDeadline("local_model_lane_deadline")
            return call(remaining)
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()
