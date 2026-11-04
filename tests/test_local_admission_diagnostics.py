"""诊断不泄露协调参数，且不能把释放未知或真实拒绝写成成功。"""
import json
import time
from unittest.mock import Mock

import pytest

from app.services.local_admission import ACQUIRE, RELEASE, RedisCoordinator
from app.services.local_inference import LocalModelBusy, inference_scope
from scripts.diagnose_local_admission import Recorder, RedisProbe


def test_probe_preserves_release_result_and_redacts_lease_and_keys():
    client = Mock()
    client.eval.return_value = 0
    recorder = Recorder()
    probe = RedisProbe(client, recorder)
    assert probe.eval(RELEASE, 1, "private-key", "private-owner-token") == 0
    row = recorder.rows[0]
    assert row["stage"] == "redis.release" and row["result"] == 0
    assert "private" not in json.dumps(row)
    client.eval.assert_called_once_with(RELEASE, 1, "private-key", "private-owner-token")


def test_probe_records_transport_failure_without_saving_exception_message():
    client = Mock()
    client.eval.side_effect = TimeoutError("redis://private-password@private-host")
    recorder = Recorder()
    with pytest.raises(TimeoutError):
        RedisProbe(client, recorder).eval(ACQUIRE, 3, "a", "b", "c", "private-token", "interactive")
    row = recorder.rows[0]
    assert row["error_type"] == "TimeoutError" and row["stage"] == "redis.acquire"
    assert "private" not in json.dumps(row) and row["elapsed_ms"] >= 0


def test_failed_release_retains_owner_until_ttl_and_diagnostic_does_not_claim_deletion():
    """复现已核实的拒绝链；仅替身，真实Redis证据独立报告。"""
    client = Mock()
    client.zrem.return_value = 0
    client.eval.side_effect = [1, TimeoutError("release response unknown"), 0]
    recorder = Recorder()
    coord = RedisCoordinator(RedisProbe(client, recorder))
    with inference_scope(events=[]):
        permit = coord.acquire(priority="interactive", deadline=time.monotonic()+5)
        permit.close()
        assert permit._lost and permit._released  # released字段只是尝试过，不等于服务端删除成功。
        # 仍有owner时新申请排队；不回退进程放行。
        assert coord.client.eval(ACQUIRE, 3, *coord.keys, "next", "interactive", 500, 15000, 1, 2) == 0
    failures = [r for r in recorder.rows if r["stage"] == "redis.release"]
    assert len(failures) == 1 and failures[0]["outcome"] == "error"


def test_scheduled_report_keeps_typed_busy_reason_without_dispatch(monkeypatch):
    from scripts.verify_gpu_pair import run_case_scheduled, workload
    monkeypatch.setattr("app.services.local_admission.get_coordinator",
                        Mock(side_effect=LocalModelBusy("local_request_coordination_unavailable")))
    encode = Mock()
    monkeypatch.setattr("app.services.embedding_service.APIEmbedding.encode_query", encode)
    row = run_case_scheduled(workload(1)[0], whole_request=True)
    assert row["status"] == "BUSY" and row["error_code"] == "local_request_coordination_unavailable"
    encode.assert_not_called()
