import json
from pathlib import Path
import pytest
from scripts.test_m1_real import verified_runtime


def configure(root: Path, *, scope="m3", owner="m3-closeout", status="running", recorded_port="62636", actual_port="62636", monkeypatch):
    path = root / f"reports/{scope}-closeout/runtime/state.json"
    path.parent.mkdir(parents=True)
    state = {"prefix": f"aicshub-{scope}-acceptance-synthetic", "containers": {"mysql": "test-mysql", "redis": "test-redis"},
             "ports": {"mysql": recorded_port, "redis": "62637"}}
    path.write_text(json.dumps(state), encoding="utf-8")

    def inspect(arguments, **kwargs):
        mysql = arguments[-1] == "test-mysql"
        port_map = {"3306/tcp" if mysql else "6379/tcp": [{"HostIp": "127.0.0.1", "HostPort": actual_port if mysql else "62637"}]}
        return owner + "|" + status + "|" + json.dumps(port_map)

    monkeypatch.setattr("scripts.test_m1_real.subprocess.check_output", inspect)
    return path


def test_explicit_m3_reuse_accepts_only_matching_running_test_containers(tmp_path, monkeypatch):
    expected = configure(tmp_path, monkeypatch=monkeypatch)
    path, state, owner = verified_runtime(tmp_path, reuse_m3=True)
    assert path == expected and owner == "m3-closeout" and state["ports"]["mysql"] == "62636"


@pytest.mark.parametrize("options,message", [
    ({"owner": "ai-customer"}, "non-test"),
    ({"status": "exited"}, "not running"),
    ({"recorded_port": "3306"}, "port does not match"),
])
def test_wrong_owner_stopped_container_or_stale_port_is_rejected_before_database_access(tmp_path, monkeypatch, options, message):
    configure(tmp_path, monkeypatch=monkeypatch, **options)
    with pytest.raises(RuntimeError, match=message):
        verified_runtime(tmp_path, reuse_m3=True)


def test_default_scope_stays_m1_and_does_not_silently_fall_back(tmp_path, monkeypatch):
    configure(tmp_path, scope="m1", owner="m1-closeout", monkeypatch=monkeypatch)
    _, _, owner = verified_runtime(tmp_path)
    assert owner == "m1-closeout"
