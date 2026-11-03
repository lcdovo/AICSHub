"""临时Git仓库验证测试证据能识别源码漂移，不读取项目.env。"""

import json
from pathlib import Path
import subprocess
import sys
import pytest
from scripts.test_provenance import snapshot_sources, compare_sources, evidence_exit_code


@pytest.fixture
def repository(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "app").mkdir()
    (tmp_path / "app/main.py").write_text("# initial\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("reports/\n.env\n", encoding="utf-8")
    git("add", "app/main.py", ".gitignore")
    git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.test", "commit", "-qm", "synthetic")
    return tmp_path


def test_dirty_and_new_source_are_fingerprinted_while_secrets_and_reports_are_excluded(repository):
    (repository / "app/main.py").write_text("# changed\n", encoding="utf-8")
    (repository / "app/new.py").write_text("# new source\n", encoding="utf-8")
    (repository / "app/.env.backup").write_text("synthetic-secret", encoding="utf-8")
    (repository / ".env").write_text("synthetic-secret", encoding="utf-8")
    (repository / "reports").mkdir()
    (repository / "reports/private.json").write_text('{"secret":"synthetic-secret"}', encoding="utf-8")
    before = snapshot_sources(repository)
    assert set(before["files"]) == {"app/main.py", "app/new.py"}
    assert before["dirty_input_paths"] == ["app/main.py", "app/new.py"]
    assert "synthetic-secret" not in json.dumps(before)
    (repository / "reports/private.json").write_text("changed private data", encoding="utf-8")
    assert compare_sources(before, snapshot_sources(repository))["source_tree_stable"]


def test_change_addition_and_deletion_prevent_green_evidence(repository):
    before = snapshot_sources(repository)
    (repository / "app/main.py").unlink()
    (repository / "app/新增.py").write_text("# synthetic\n", encoding="utf-8")
    after = snapshot_sources(repository)
    comparison = compare_sources(before, after)
    assert comparison["changed_input_paths"] == ["app/main.py", "app/新增.py"]
    assert comparison["source_tree_stable"] is False
    assert evidence_exit_code(0, comparison) == 2
    assert evidence_exit_code(1, comparison) == 1


def test_same_source_after_commit_has_same_fingerprint_independent_of_dirty_status(repository):
    (repository / "app/main.py").write_text("# updated\n", encoding="utf-8")
    before = snapshot_sources(repository)
    subprocess.run(["git", "add", "app/main.py"], cwd=repository, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.test",
                    "commit", "-qm", "next"], cwd=repository, check=True, capture_output=True)
    after = snapshot_sources(repository)
    assert before["dirty_input_paths"] == ["app/main.py"] and after["dirty_input_paths"] == []
    assert before["fingerprint"] == after["fingerprint"]
    assert evidence_exit_code(0, compare_sources(before, after)) == 0


def test_actual_offline_runner_rejects_successful_tests_when_source_changes(repository):
    # 在另一临时仓库运行真实执行器，不修改本项目或业务配置。
    project_root = Path(__file__).resolve().parents[1]
    (repository / "scripts").mkdir()
    for name in ("test_offline.py", "test_provenance.py"):
        (repository / "scripts" / name).write_bytes((project_root / "scripts" / name).read_bytes())
    (repository / "app/config").mkdir()
    (repository / "app/config/config.py").write_text("class Settings:\n    TEST_INPUT: str = ''\n", encoding="utf-8")
    (repository / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repository / "tests").mkdir()
    (repository / "tests/test_mutation.py").write_text(
        "from pathlib import Path\n"
        "def test_mutation():\n"
        "    source = Path(__file__).resolve().parents[1] / 'app/main.py'\n"
        "    source.write_text('# changed during passing test\\n', encoding='utf-8')\n"
        "    assert True\n", encoding="utf-8"
    )
    run = subprocess.run([sys.executable, "scripts/test_offline.py", "-q", "tests/test_mutation.py"],
                         cwd=repository, capture_output=True, timeout=60)
    assert run.returncode == 2, run.stdout.decode(errors="replace") + run.stderr.decode(errors="replace")
    report = next((repository / "reports/offline").glob("*/manifest.json"))
    manifest = json.loads(report.read_text(encoding="utf-8"))
    assert manifest["pytest_exit_code"] == 0 and manifest["exit_code"] == 2
    assert manifest["source_tree_stable"] is False
    assert manifest["changed_input_paths"] == ["app/main.py"]
    assert (report.parent / "source-start.json").is_file()
    assert (report.parent / "source-end.json").is_file()
