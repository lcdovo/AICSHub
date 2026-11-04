"""释放回包未知时有界补偿，不能删除继任者或冒充释放成功。"""
import json
import time
from unittest.mock import Mock

import pytest

from app.services.local_admission import Permit, RELEASE, RedisCoordinator, request_scope
from app.services.local_inference import inference_scope


def permit_with_results(results):
    client = Mock()
    client.eval.side_effect = results
    coord = RedisCoordinator(client)
    coord.renew_seconds = 60
    return Permit(coord, "private-owner", time.monotonic()+5, 0), client


def test_release_timeout_then_success_is_bounded_and_confirmed():
    permit, client = permit_with_results([TimeoutError("private-address"), 1])
    permit.close()
    permit.close()
    assert client.eval.call_count == 2
    assert all(c.args[0] == RELEASE for c in client.eval.call_args_list)
    assert permit.release_status == "released" and permit._released
    assert permit.release_attempts == 2 and permit._lost


def test_persistent_release_failure_stays_unknown_without_unbounded_close_retry():
    permit, client = permit_with_results(TimeoutError("private-address"))
    permit.close()
    permit.close()
    assert client.eval.call_count == 2
    assert permit.release_status == "unknown" and not permit._released
    assert permit._stop.is_set() and permit._lost


def test_deleted_owner_with_lost_reply_is_not_claimed_as_confirmed_delete():
    permit, client = permit_with_results([TimeoutError("lost reply"), 0])
    permit.close()
    assert permit.release_status == "not_owner" and not permit._released
    assert permit.release_attempts == 2


def test_successor_after_unknown_release_cannot_be_deleted():
    permit, client = permit_with_results([])
    owner = {"value": permit.token}
    def release(script, count, key, token):
        assert script == RELEASE and token == "private-owner"
        if client.eval.call_count == 1:
            owner["value"] = "successor"
            raise TimeoutError("old result unknown")
        return int(owner["value"] == token)
    client.eval.side_effect = release
    permit.close()
    assert owner["value"] == "successor"
    assert permit.release_status == "not_owner"


def test_inflight_http_prevents_release_compensation_until_return():
    permit, client = permit_with_results([TimeoutError("temporary"), 1])
    client.get.return_value = permit.token
    def call(_):
        permit.close()
        assert client.eval.call_count == 0
        assert permit.release_status == "pending"
        return "late-result"
    from app.services.local_inference import LocalModelDeadline
    with pytest.raises(LocalModelDeadline):
        permit.http(call, purpose="synthetic")
    assert client.eval.call_count == 2 and permit.release_status == "released"


def test_release_observation_does_not_expose_owner_or_exception(monkeypatch):
    permit, _ = permit_with_results(TimeoutError("private-address"))
    monkeypatch.setattr(permit.coordinator, "acquire", lambda **_: permit)
    monkeypatch.setattr(permit.coordinator, "valid", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.services.local_admission.get_coordinator", lambda: permit.coordinator)
    events = []
    with inference_scope(events=events), request_scope():
        pass
    assert events[-1]["lease_release"]["status"] == "unknown"
    assert events[-1]["lease_release"]["attempts"] == 2
    assert "private" not in json.dumps(events)


def test_cleanup_failure_for_front_ticket_still_attempts_background():
    client = Mock()
    client.eval.return_value = -1
    client.zrem.side_effect = [TimeoutError("front"), 0]
    coord = RedisCoordinator(client)
    from app.services.local_inference import LocalModelBusy
    with pytest.raises(LocalModelBusy):
        coord.acquire(priority="interactive", deadline=time.monotonic()+5)
    assert [call.args[0] for call in client.zrem.call_args_list] == coord.keys[1:]
