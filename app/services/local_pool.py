"""Cross-process two-slot GPU leases, compatible with the exclusive owner gate.

The legacy owner key is held as a marker while any pool lease is active, so
old one-slot clients cannot overlap the pool. A foreign legacy owner blocks
pool admission too. Capacity disagreement fails closed while leases exist.
"""
from __future__ import annotations

import time
import uuid

from app.services.local_admission import Permit
from app.services.local_inference import LocalModelBusy

MARKER = "__gpu_pool_v1__"
ACQUIRE_POOL = """
local t=redis.call('TIME'); local now=t[1]*1000+math.floor(t[2]/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',now)
redis.call('ZREMRANGEBYSCORE',KEYS[3],'-inf',now)
redis.call('ZREMRANGEBYSCORE',KEYS[4],'-inf',now)
local gate=redis.call('GET',KEYS[1])
local owners=redis.call('ZCARD',KEYS[2])
if owners>0 and redis.call('GET',KEYS[5])~=ARGV[6] then return -3 end
local q=KEYS[3]; if ARGV[2]=='background' then q=KEYS[4] end
if not redis.call('ZSCORE',q,ARGV[1]) then
 if ARGV[5]=='0' then return -2 end
 if redis.call('ZCARD',KEYS[3])+redis.call('ZCARD',KEYS[4])>=2 then return -1 end
 redis.call('ZADD',q,now+tonumber(ARGV[3]),ARGV[1]);redis.call('PEXPIRE',q,2000)
end
local f=redis.call('ZRANGE',KEYS[3],0,0); local b=redis.call('ZRANGE',KEYS[4],0,0)
local background_free=true
if ARGV[2]=='background' then
 for _,member in ipairs(redis.call('ZRANGE',KEYS[2],0,-1)) do
  if string.sub(member,1,11)=='background:' then background_free=false end
 end
end
if (not gate or gate==ARGV[7]) and owners<tonumber(ARGV[6]) and
 background_free and
 (f[1]==ARGV[1] or (not f[1] and b[1]==ARGV[1])) then
 redis.call('ZREM',q,ARGV[1])
 redis.call('ZADD',KEYS[2],now+tonumber(ARGV[4]),ARGV[1])
 redis.call('SET',KEYS[5],ARGV[6],'PX',ARGV[4])
 redis.call('SET',KEYS[1],ARGV[7],'PX',ARGV[4]);return 1
end
return 0
"""
CHECK_POOL = """
local t=redis.call('TIME'); local now=t[1]*1000+math.floor(t[2]/1000)
local expiry=redis.call('ZSCORE',KEYS[2],ARGV[1])
if not expiry or tonumber(expiry)<=now or redis.call('GET',KEYS[1])~=ARGV[4] then return 0 end
if ARGV[3]=='1' then
 redis.call('ZADD',KEYS[2],now+tonumber(ARGV[2]),ARGV[1])
 redis.call('PEXPIRE',KEYS[1],ARGV[2]);redis.call('PEXPIRE',KEYS[3],ARGV[2])
end
return 1
"""
RELEASE_POOL = """
local removed=redis.call('ZREM',KEYS[2],ARGV[1])
local t=redis.call('TIME'); local now=t[1]*1000+math.floor(t[2]/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',now)
if redis.call('ZCARD',KEYS[2])==0 and redis.call('GET',KEYS[1])==ARGV[2] then
 redis.call('DEL',KEYS[1],KEYS[3])
end
return removed
"""


class RedisPoolCoordinator:
    scope = "redis"

    def __init__(self, client, namespace: str, capacity: int = 2, ttl_ms: int = 15000):
        if capacity != 2:
            raise ValueError("gpu_pool_capacity_must_be_two")
        self.client, self.capacity, self.ttl_ms = client, capacity, ttl_ms
        self.renew_seconds = ttl_ms / 4000
        self.keys = [namespace + suffix for suffix in (":owner", ":pool:owners", ":pool:interactive", ":pool:background", ":pool:capacity")]

    def acquire(self, *, priority, deadline):
        if priority not in {"interactive", "background"}:
            raise ValueError("invalid_local_priority")
        began = time.monotonic()
        until = min(deadline, began + (.5 if priority == "interactive" else .05))
        token, first = priority + ":" + uuid.uuid4().hex, True
        try:
            while time.monotonic() < until:
                try:
                    result = self.client.eval(ACQUIRE_POOL, 5, *self.keys, token, priority,
                        max(1, int((until-began)*1000)), self.ttl_ms, int(first), self.capacity, MARKER)
                except Exception:
                    try:
                        self.release(token)
                    except Exception:
                        pass
                    raise LocalModelBusy("local_pool_coordination_unavailable") from None
                first = False
                if result == 1:
                    if time.monotonic() >= until:
                        self.release(token)
                        raise LocalModelBusy("late_local_pool_admission")
                    return Permit(self, token, deadline, (time.monotonic()-began)*1000)
                if result < 0:
                    raise LocalModelBusy("local_pool_queue_full_or_mismatched")
                time.sleep(min(.01, max(0, until-time.monotonic())))
            raise LocalModelBusy("local_pool_wait_expired")
        finally:
            for key in self.keys[2:4]:
                try:
                    self.client.zrem(key, token)
                except Exception:
                    pass

    def valid(self, token, renew=False):
        return bool(self.client.eval(CHECK_POOL, 3, self.keys[0], self.keys[1], self.keys[4],
            token, self.ttl_ms, int(renew), MARKER))

    def release(self, token):
        return self.client.eval(RELEASE_POOL, 3, self.keys[0], self.keys[1], self.keys[4], token, MARKER)
