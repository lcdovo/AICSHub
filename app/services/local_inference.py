"""本进程本地模型HTTP出口共享有界调度；不提供跨进程/GPU驱动级互斥。"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import threading
import time
from urllib.parse import urlsplit
import httpx


class LocalModelBusy(RuntimeError):
    pass


class LocalModelDeadline(TimeoutError):
    pass


@dataclass(frozen=True)
class InferenceContext:
    priority: str = "interactive"
    deadline: float | None = None
    events: list | None = None


CONTEXT: ContextVar[InferenceContext] = ContextVar("local_inference_context", default=InferenceContext())
SCHEDULER_VERSION = "process-local-active1-waiters2-priority-v2"


@contextmanager
def inference_scope(*, priority=None, deadline=None, events=None):
    parent = CONTEXT.get()
    expires = deadline if parent.deadline is None else parent.deadline if deadline is None else min(parent.deadline, deadline)
    token = CONTEXT.set(InferenceContext(priority or parent.priority, expires,
                                         parent.events if events is None else events))
    try:
        yield
    finally:
        CONTEXT.reset(token)


def is_local_endpoint(base: str) -> bool:
    return urlsplit(base).hostname in {"127.0.0.1", "localhost", "::1", "embedding-local", "reranker",
                                      "embedding-gpu", "reranker-gpu"}


class LocalInferenceScheduler:
    """无后台线程队列：调用方有界等待，实际执行返回前不释放槽位。"""
    def __init__(self, *, max_waiters=2, queue_seconds=.5, phase_seconds=5.0):
        self.max_waiters, self.queue_seconds, self.phase_seconds = max_waiters, queue_seconds, phase_seconds
        self._condition = threading.Condition()
        self._interactive = deque()
        self._background = deque()
        self._active = False

    def run(self, call, *, purpose: str, deadline=None):
        context = CONTEXT.get()
        began = time.monotonic()
        deadline = min(d for d in [began + self.phase_seconds, context.deadline, deadline] if d is not None)
        wait_until = min(deadline, began + (self.queue_seconds if context.priority == "interactive" else .05))
        marker = object()
        queue = self._interactive if context.priority == "interactive" else self._background
        entered = False
        queued = False
        waited = False
        dispatched_at = None
        status = "failed"
        try:
            with self._condition:
                if began >= deadline:
                    status = "deadline"
                    raise LocalModelDeadline("local_model_deadline_before_queue")
                if (len(self._interactive) + len(self._background) >= self.max_waiters
                        and (self._active or self._interactive or self._background)):
                    status = "busy"
                    raise LocalModelBusy("local_model_queue_full")
                queue.append(marker)
                queued = True
                while True:
                    current = time.monotonic()
                    if current >= deadline:
                        status = "deadline"
                        raise LocalModelDeadline("local_model_deadline_in_queue")
                    if waited and current >= wait_until:
                        status = "busy"
                        raise LocalModelBusy("local_model_queue_wait_exhausted")
                    first = self._interactive[0] if self._interactive else self._background[0]
                    if not self._active and marker is first:
                        queue.remove(marker)
                        queued = False
                        self._active = entered = True
                        dispatched_at = current
                        break
                    if current >= wait_until:
                        status = "busy"
                        raise LocalModelBusy("local_model_queue_wait_exhausted")
                    waited = True
                    self._condition.wait(timeout=max(0, wait_until - current))
            result = call(deadline - time.monotonic())
            if time.monotonic() >= deadline:
                status = "deadline"
                raise LocalModelDeadline("late_local_model_result_rejected")
            status = "succeeded"
            return result
        except (LocalModelDeadline, httpx.TimeoutException, TimeoutError):
            status = "deadline"
            raise LocalModelDeadline("local_model_deadline_or_http_timeout") from None
        finally:
            ended = time.monotonic()
            with self._condition:
                if queued:
                    queue.remove(marker)
                if entered:
                    self._active = False
                self._condition.notify_all()
            if context.events is not None:
                # 只输出固定用途/时间/结论；不记录用户文本、URL、标识符或凭据。
                context.events.append({"purpose": purpose, "priority": context.priority, "status": status,
                                       "dispatched": entered,
                                       "queue_ms": round(((dispatched_at or ended) - began) * 1000, 3),
                                       "inference_ms": round((ended - dispatched_at) * 1000, 3) if entered else None})


SCHEDULER = LocalInferenceScheduler()
