"""Real Redis Lua integration in a unique namespace, opt-in on a local runtime."""
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.local_admission import RedisCoordinator
from app.services.local_pool import RedisPoolCoordinator
from app.services.local_inference import LocalModelBusy


@pytest.fixture
def pool():
    url = os.environ.get("TEST_GPU_POOL_REDIS_URL")
    if not url:
        pytest.skip("isolated Redis integration requires TEST_GPU_POOL_REDIS_URL")
    import redis
    client = redis.Redis.from_url(url, socket_timeout=.3)
    namespace = "aicshub:test:gpu-pool:{" + uuid.uuid4().hex + "}"
    value = RedisPoolCoordinator(client, namespace)
    try:
        yield value, RedisCoordinator(client, namespace)
    finally:
        keys = list(client.scan_iter(namespace + "*"))
        if keys:
            client.delete(*keys)
        client.close()


def acquire(coord):
    return coord.acquire(priority="interactive", deadline=time.monotonic()+3)


def test_two_slots_shared_between_independent_clients(pool):
    first, legacy = pool
    second = RedisPoolCoordinator(first.client, legacy.keys[0][:-6])
    a, b = acquire(first), acquire(second)
    try:
        assert first.valid(a.token) and second.valid(b.token, renew=True)
        with pytest.raises(LocalModelBusy):
            acquire(first)
        with pytest.raises(LocalModelBusy):
            acquire(legacy)
        a.close()
        assert second.valid(b.token)
        c = acquire(first)
        c.close()
    finally:
        a.close(); b.close()
    old = acquire(legacy)
    try:
        with pytest.raises(LocalModelBusy):
            acquire(first)
    finally:
        old.close()


def test_foreign_release_and_expired_lease_cannot_restore_ownership(pool):
    coord, _ = pool
    a = acquire(coord)
    try:
        assert coord.release("foreign") == 0
        assert coord.valid(a.token)
        coord.client.zadd(coord.keys[1], {a.token: 1})
        assert not coord.valid(a.token, renew=True)
        assert not coord.valid(a.token)
    finally:
        a.close()


def test_parallel_acquisition_never_exceeds_two(pool):
    coord, _ = pool
    import threading
    lock = threading.Lock()
    active = peak = admitted = 0
    def job(_):
        nonlocal active, peak, admitted
        try:
            permit = acquire(coord)
        except LocalModelBusy:
            return
        try:
            with lock:
                active += 1; admitted += 1; peak = max(peak, active)
            time.sleep(.03)
            assert permit.coordinator.valid(permit.token)
        finally:
            with lock:
                active -= 1
            permit.close()
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(job, range(20)))
    assert admitted >= 2 and peak == 2


def test_coordinator_unavailable_fails_before_dispatch(pool):
    coord, _ = pool
    from unittest.mock import Mock
    coord.client = Mock()
    coord.client.eval.side_effect = ConnectionError("synthetic")
    with pytest.raises(LocalModelBusy):
        acquire(coord)


def test_background_uses_at_most_one_slot(pool):
    coord, _ = pool
    background = coord.acquire(priority="background", deadline=time.monotonic()+3)
    try:
        with pytest.raises(LocalModelBusy):
            coord.acquire(priority="background", deadline=time.monotonic()+3)
        foreground = acquire(coord)
        foreground.close()
        assert coord.valid(background.token)
    finally:
        background.close()


def test_lost_gate_cannot_be_renewed_or_released_as_another_owner(pool):
    coord, _ = pool
    permit = acquire(coord)
    try:
        coord.client.set(coord.keys[0], "another-owner", px=15000)
        assert not coord.valid(permit.token, renew=True)
        permit.close()
        assert coord.client.get(coord.keys[0]) == b"another-owner"
    finally:
        permit.close()
