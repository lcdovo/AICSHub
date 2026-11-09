"""Subprocess checks in synthetic repositories: never load business secrets."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tests.test_test_provenance import repository as repository

ROOT = Path(__file__).resolve().parents[1]


def prepare(repository, body):
    (repository / "scripts").mkdir()
    for name in ("test_offline.py", "test_provenance.py"):
        shutil.copy2(ROOT / "scripts" / name, repository / "scripts" / name)
    (repository / "app/config").mkdir()
    (repository / "app/config/config.py").write_text(
        "class Settings:\n    LLM_API_KEY: str = ''\n    TEST_INPUT: str = ''\n", encoding="utf-8")
    (repository / "tests").mkdir()
    (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repository / "tests/test_probe.py").write_text(body, encoding="utf-8")


def run(repository):
    result = subprocess.run([sys.executable, "scripts/test_offline.py", "-q", "tests/test_probe.py"],
                            cwd=repository, capture_output=True, timeout=60)
    report = next((repository / "reports/offline").glob("*/manifest.json"))
    return result, json.loads(report.read_text(encoding="utf-8"))


def test_runner_drops_inherited_keys_and_ignores_root_env(repository, monkeypatch):
    prepare(repository, "import os\nfrom pathlib import Path\ndef test_probe():\n"
            "    assert os.environ.get('LLM_API_KEY') == ''\n"
            "    assert 'OPENAI_API_KEY' not in os.environ\n"
            "    assert 'TEST_INPUT' not in os.environ\n"
            "    assert not Path('.env').exists()\n")
    (repository / ".env").write_text("LLM_API_KEY=synthetic-file-secret\nTEST_INPUT=synthetic\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-inherited-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-sdk-secret")
    monkeypatch.setenv("TEST_INPUT", "synthetic")
    result, manifest = run(repository)
    assert result.returncode == manifest["exit_code"] == 0
    assert manifest["blocked_network_operations"] == 0
    assert "synthetic-inherited-secret" not in json.dumps(manifest)


def test_caught_network_attempt_still_rejects_green_evidence(repository):
    prepare(repository, "import socket\ndef test_probe():\n"
            "    try: socket.getaddrinfo('synthetic.invalid',443)\n"
            "    except RuntimeError: pass\n"
            "    else: raise AssertionError('network guard missing')\n")
    result, manifest = run(repository)
    assert result.returncode == manifest["exit_code"] == 2
    assert manifest["pytest_exit_code"] == 0
    assert manifest["blocked_network_operations"] == 1


def test_direct_pytest_refuses_before_test_module_import(repository):
    prepare(repository, "raise AssertionError('test imported before isolation')\n")
    shutil.copy2(ROOT / "tests/conftest.py", repository / "tests/conftest.py")
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                            cwd=repository, capture_output=True, timeout=60)
    output = (result.stdout + result.stderr).decode(errors="replace")
    assert result.returncode != 0
    assert "Unsafe direct pytest invocation blocked" in output
    assert "AssertionError: test imported before isolation" not in output
