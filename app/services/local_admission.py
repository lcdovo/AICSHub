"""整轮本地模型准入；Redis可选共享租约，失联拒绝，不提供GPU任务取消。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections import deque
import threading
import time
import uuid

from app.services.local_inference import CONTEXT, LocalModelBusy, LocalModelDeadline

VERSION = "whole-request-owner-lease-v1"
CURRENT: ContextVar[Permit | None] = ContextVar("local_request_permit", default=None)

ACQUIRE = """
local t=redis.call('TIME'); local now=t[1]*1000+math.floor(t[2]/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',now)
redis.call('ZREMRANGEBYSCORE',KEYS[3],'-inf',now)
local owner=redis.call('GET',KEYS[1])
if owner==ARGV[1] then return 1 end
local q=KEYS[2]; if ARGV[2]=='background' then q=KEYS[3] end
local present=redis.call('ZSCORE',q,ARGV[1])
local n=redis.call('ZCARD',KEYS[2])+redis.call('ZCARD',KEYS[3])
if not present then
 if ARGV[5]=='0' then return -2 end
 if not owner and n==0 then redis.call('SET',KEYS[1],ARGV[1],'PX',ARGV[4]);return 1 end
 if n>=tonumber(ARGV[6]) then return -1 end
 redis.call('ZADD',q,now+tonumber(ARGV[3]),ARGV[1])
 redis.call('PEXPIRE',q,2000)
end
local f=redis.call('ZRANGE',KEYS[2],0,0)
local b=redis.call('ZRANGE',KEYS[3],0,0)
if not owner and (f[1]==ARGV[1] or (not f[1] and b[1]==ARGV[1])) then
 redis.call('ZREM',q,ARGV[1]);redis.call('SET',KEYS[1],ARGV[1],'PX',ARGV[4]);return 1
end
return 0
"""
RENEW = "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('PEXPIRE',KEYS[1],ARGV[2]) else return 0 end"
RELEASE = "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end"


class Permit:
    def __init__(self, coordinator, token, deadline, queue_ms):
        self.coordinator, self.token, self.deadline, self.queue_ms = coordinator, token, deadline, queue_ms
        self._state = threading.Lock()
        self._http = threading.Lock()
        self._refs = 0
        self._closed = False
        self._lost = False
        self._released = False
        self._stop = threading.Event()
        self._heartbeat = None
        if coordinator.scope == "redis":
            self._heartbeat = threading.Thread(target=self._renew_loop, daemon=True, name="local-model-lease")
            self._heartbeat.start()

    def _renew_loop(self):
        while not self._stop.wait(self.coordinator.renew_seconds):
            try:
                valid = self.coordinator.valid(self.token, renew=True)
            except Exception:
                valid = False
            if not valid:
                self._lost = True
                return

    def check(self):
        if self._lost or time.monotonic() >= self.deadline:
            raise LocalModelDeadline("local_request_deadline_or_lost_lease")
        try:
            valid = self.coordinator.valid(self.token)
        except Exception:
            valid = False
        if not valid:
            self._lost = True
            raise LocalModelDeadline("local_request_lost_lease")

    def http(self, call, *, purpose, deadline=None):
        import httpx
        context = CONTEXT.get()
        began = time.monotonic()
        expires = min(d for d in (self.deadline, context.deadline, deadline, began + 5) if d is not None)
        status, dispatched, acquired = "failed", False, False
        sent = None
        with self._state:
            if self._closed:
                raise LocalModelDeadline("closed_local_request")
            self._refs += 1
        try:
            if not self._http.acquire(timeout=max(0, expires-time.monotonic())):
                raise LocalModelDeadline("local_request_nested_http_deadline")
            acquired = True
            self.check()
            if self._closed or time.monotonic() >= expires:
                raise LocalModelDeadline("local_request_http_expired")
            sent = time.monotonic()
            dispatched = True
            result = call(expires-sent)
            self.check()
            if self._closed or time.monotonic() >= expires:
                raise LocalModelDeadline("late_local_request_http")
            status = "succeeded"
            return result
        except (TimeoutError, httpx.TimeoutException):
            status = "deadline"
            raise LocalModelDeadline("local_request_http_deadline") from None
        finally:
            ended = time.monotonic()
            if acquired:
                self._http.release()
            if context.events is not None:
                context.events.append({"purpose":purpose, "priority":context.priority, "status":status,
                    "dispatched":dispatched,"queue_ms":round(((sent or ended)-began)*1000,3),
                    "inference_ms":round((ended-sent)*1000,3) if sent is not None else None})
            with self._state:
                self._refs -= 1
                self._maybe_release()

    def _maybe_release(self):
        if self._closed and self._refs == 0 and not self._released:
            self._released = True
            self._stop.set()
            try:
                self.coordinator.release(self.token)
            except Exception:
                # Redis TTL回收，不能无条件删别人的owner。
                self._lost = True

    def close(self):
        with self._state:
            self._closed = True
            self._maybe_release()


class ProcessCoordinator:
    scope = "process"
    def __init__(self, max_waiters=2):
        self.condition = threading.Condition()
        self.owner = None
        self.waiters = {"interactive":deque(), "background":deque()}
        self.max_waiters = max_waiters

    def acquire(self, *, priority, deadline):
        began = time.monotonic()
        until = min(deadline, began + (.5 if priority=="interactive" else .05))
        token = uuid.uuid4().hex
        with self.condition:
            if time.monotonic() >= deadline:
                raise LocalModelDeadline("local_request_expired")
            count = sum(map(len,self.waiters.values()))
            if count >= self.max_waiters and (self.owner or count):
                raise LocalModelBusy("local_request_queue_full")
            queue = self.waiters[priority]
            queue.append(token)
            try:
                while True:
                    now = time.monotonic()
                    if now >= until:
                        raise LocalModelBusy("local_request_wait_expired")
                    first = self.waiters["interactive"] or self.waiters["background"]
                    if self.owner is None and first[0] == token:
                        queue.remove(token)
                        self.owner = token
                        return Permit(self, token, deadline, (now-began)*1000)
                    self.condition.wait(until-now)
            finally:
                if token in queue:
                    queue.remove(token)
                self.condition.notify_all()

    def valid(self, token, renew=False):
        with self.condition:
            return self.owner == token

    def release(self, token):
        with self.condition:
            if self.owner == token:
                self.owner = None
            self.condition.notify_all()


class RedisCoordinator:
    scope = "redis"
    def __init__(self, client, namespace="aicshub:local-models:whole-v1", ttl_ms=15000):
        self.client, self.ttl_ms = client, ttl_ms
        self.renew_seconds = ttl_ms/4000
        self.keys = [namespace+":owner", namespace+":interactive", namespace+":background"]

    def acquire(self, *, priority, deadline):
        began = time.monotonic()
        until = min(deadline, began+(.5 if priority=="interactive" else .05))
        token = uuid.uuid4().hex
        first = True
        try:
            while time.monotonic() < until:
                try:
                    result = self.client.eval(ACQUIRE, 3, *self.keys, token, priority,
                                              max(1,int((until-began)*1000)), self.ttl_ms, int(first), 2)
                except Exception:
                    # EVAL可能已成功但回包丢失。尚未调用模型，可安全按token条件清理；
                    # 清理也失败时只等TTL，不回退进程放行。
                    try:
                        self.release(token)
                    except Exception:
                        pass
                    raise LocalModelBusy("local_request_coordination_unavailable") from None
                first = False
                if result == 1:
                    if time.monotonic() >= until:
                        self.release(token)
                        raise LocalModelBusy("late_local_request_admission")
                    return Permit(self,token,deadline,(time.monotonic()-began)*1000)
                if result < 0:
                    raise LocalModelBusy("local_request_queue_full_or_expired")
                time.sleep(min(.01,max(0,until-time.monotonic())))
            raise LocalModelBusy("local_request_wait_expired")
        finally:
            # 仅清自己的排队ticket；获得的owner交给Permit持有。
            try:
                self.client.zrem(self.keys[1], token)
                self.client.zrem(self.keys[2], token)
            except Exception:
                pass

    def valid(self, token, renew=False):
        if renew:
            return bool(self.client.eval(RENEW,1,self.keys[0],token,self.ttl_ms))
        value = self.client.get(self.keys[0])
        return value in (token,token.encode())

    def release(self, token):
        return self.client.eval(RELEASE,1,self.keys[0],token)


PROCESS = ProcessCoordinator()
_redis_lock = threading.Lock()
_redis_coordinator = None
_redis_identity = None


def get_coordinator():
    from app.config.config import settings
    if settings.LOCAL_MODEL_COORDINATOR == "process":
        return PROCESS
    if not settings.REDIS_ENABLED:
        raise LocalModelBusy("local_request_redis_required")
    global _redis_coordinator, _redis_identity
    identity = (settings.REDIS_URL, settings.LOCAL_MODEL_COORDINATOR_NAMESPACE)
    with _redis_lock:
        if identity != _redis_identity:
            import redis
            client = redis.Redis.from_url(settings.REDIS_URL,socket_timeout=.2,socket_connect_timeout=.2)
            _redis_coordinator = RedisCoordinator(client,settings.LOCAL_MODEL_COORDINATOR_NAMESPACE)
            _redis_identity = identity
        return _redis_coordinator


@contextmanager
def request_scope(*, purpose="retrieval"):
    parent = CURRENT.get()
    if parent is not None:
        parent.check()
        yield parent
        return
    context = CONTEXT.get()
    deadline = min(time.monotonic()+10, context.deadline or float("inf"))
    began = time.monotonic()
    permit = None
    state = "busy"
    token = None
    try:
        coordinator = get_coordinator()
        permit = coordinator.acquire(priority=context.priority,deadline=deadline)
        token = CURRENT.set(permit)
        state = "admitted"
        yield permit
        permit.check()
    except LocalModelDeadline:
        state = "deadline"
        raise
    finally:
        if token is not None:
            CURRENT.reset(token)
        if permit is not None:
            permit.close()
        if context.events is not None:
            context.events.append({"purpose":purpose,"priority":context.priority,"status":state,
                "dispatched":False,"queue_ms":round(permit.queue_ms if permit else (time.monotonic()-began)*1000,3),
                "inference_ms":None,"admission_scope":permit.coordinator.scope if permit else "not_acquired"})


def run_http(call, *, purpose, deadline=None):
    """已有整轮资格时不再排第二次队；独立调用也必须参与同一协调器。"""
    import httpx
    try:
        with request_scope(purpose="standalone_model" if CURRENT.get() is None else purpose) as permit:
            return permit.http(call,purpose=purpose,deadline=deadline)
    except httpx.TimeoutException:
        raise LocalModelDeadline("local_request_http_timeout") from None


def uses_local_models(embedding=None, reranker=None):
    from app.services.local_inference import is_local_endpoint
    backend = getattr(embedding, "_embedding", embedding)
    if backend is not None and is_local_endpoint(getattr(backend,"api_base","")):
        return True
    return any(is_local_endpoint(provider.base) for provider in getattr(reranker,"_providers",[]))
