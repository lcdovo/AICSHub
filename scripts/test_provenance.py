"""只记录允许的测试源码路径/摘要，不读取业务.env、数据卷或报告内容。"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess

SOURCE_PREFIXES = (
    "app/", "scripts/", "tests/", "acceptance/", "alembic/",
    "frontend/src/", "frontend/tests/",
)
SOURCE_SUFFIXES = {".py", ".json", ".jsonl", ".ts", ".tsx", ".js", ".sql", ".yaml", ".yml", ".txt", ".md"}
ROOT_INPUTS = {
    "pytest.ini", "pyproject.toml", "setup.cfg", "alembic.ini",
    "requirements.txt", "requirements.lock", "docker-compose.yml", "Dockerfile",
    "frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.ts",
    "frontend/tsconfig.json", "frontend/vitest.config.ts",
}


def _git_paths(root: Path, *arguments: str) -> set[str]:
    raw = subprocess.check_output(["git", *arguments, "-z"], cwd=root)
    return {path.decode("utf-8", errors="surrogateescape") for path in raw.split(b"\0") if path}


def is_test_input(path: str) -> bool:
    name = Path(path).name
    if name == ".env" or name.startswith(".env."):
        return False
    return path in ROOT_INPUTS or (
        path.startswith(SOURCE_PREFIXES) and Path(path).suffix in SOURCE_SUFFIXES
    )


def snapshot_sources(root: Path) -> dict:
    root = root.resolve()
    paths = _git_paths(root, "ls-files", "--cached", "--others", "--exclude-standard")
    deleted = _git_paths(root, "ls-files", "--deleted")
    # A stable Git deletion is part of the source state, not an unreadable file.
    # Deletions during a run still change the before/after file map and fail evidence.
    paths -= deleted
    dirty = (_git_paths(root, "diff", "HEAD", "--name-only")
             | _git_paths(root, "ls-files", "--others", "--exclude-standard"))
    hashes = {}
    for relative in sorted(path for path in paths if is_test_input(path)):
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            # 不为源码指纹追踪工作区外的链接或读取其内容。
            hashes[relative] = "unsupported_link"
        elif path.is_file():
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            hashes[relative] = "missing"
    encoded = json.dumps(hashes, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return {
        "schema": 1,
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "files": hashes,
        "dirty_input_paths": sorted(path for path in dirty if is_test_input(path)),
        "deleted_input_paths": sorted(path for path in deleted if is_test_input(path)),
        "scope": "allowlisted source/config from git tracked and nonignored untracked paths; no env/data/reports",
        "unsupported_paths": sorted(path for path, digest in hashes.items() if digest in {"unsupported_link", "missing"}),
    }


def compare_sources(before: dict, after: dict) -> dict:
    left, right = before["files"], after["files"]
    changed = sorted(path for path in left.keys() | right.keys() if left.get(path) != right.get(path))
    complete = not (before["unsupported_paths"] or after["unsupported_paths"])
    return {
        "source_tree_stable": not changed,
        "source_snapshot_complete": complete,
        "changed_input_paths": changed,
        "start_source_fingerprint": before["fingerprint"],
        "end_source_fingerprint": after["fingerprint"],
    }


def evidence_exit_code(pytest_exit_code: int, comparison: dict) -> int:
    if pytest_exit_code:
        return pytest_exit_code
    # 用例全绿但源码漂移/读取不完整不能作为可复用的通过证据。
    return 0 if comparison["source_tree_stable"] and comparison["source_snapshot_complete"] else 2


def snapshot_filesystem_sources(root: Path) -> dict:
    """Explicit container snapshot; never infer a Git commit for an image."""
    root = root.resolve()
    paths = set()
    for prefix in SOURCE_PREFIXES:
        directory = root / prefix
        if directory.is_dir():
            paths.update(p.relative_to(root).as_posix() for p in directory.rglob("*")
                         if (p.is_file() or p.is_symlink()) and is_test_input(p.relative_to(root).as_posix()))
    paths.update(name for name in ROOT_INPUTS if (root / name).is_file())
    hashes = {}
    for relative in sorted(paths):
        path = root / relative
        hashes[relative] = ("unsupported_link" if path.is_symlink() or not path.resolve().is_relative_to(root)
                            else hashlib.sha256(path.read_bytes()).hexdigest())
    encoded = json.dumps(hashes, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return {"schema": 1, "fingerprint": hashlib.sha256(encoded).hexdigest(), "files": hashes,
            "dirty_input_paths": [], "deleted_input_paths": [],
            "unsupported_paths": [k for k,v in hashes.items() if v == "unsupported_link"],
            "source_mode": "filesystem"}
