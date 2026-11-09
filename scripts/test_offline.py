"""T0 离线回归入口：临时工作目录、独立 SQLite、禁止网络连接。

运行：python scripts/test_offline.py [-q] [tests/test_refund_flow.py ...]
完整输出、JUnit 和配置指纹保存在被 Git 忽略的 reports/offline/ 下。
此入口不是 T1 真实依赖或完整验收执行器。
"""
from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading


class Tee:
    def __init__(self, stream, log):
        self.stream = stream
        self.log = log

    def write(self, value: str) -> int:
        self.log.write(value)
        self.log.flush()
        return self.stream.write(value)

    def flush(self) -> None:
        self.stream.flush()
        self.log.flush()

    def __getattr__(self, name: str):
        return getattr(self.stream, name)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile", choices=("baseline", "active"), default="baseline")
    parser.add_argument("--source-mode", choices=("git", "filesystem"), default="git")
    parser.add_argument("--image-id", default=None)
    options, test_arguments = parser.parse_known_args()
    if options.source_mode == "filesystem" and not options.image_id:
        parser.error("filesystem evidence requires --image-id")
    root = Path(__file__).resolve().parents[1]
    output = root / "reports" / "offline" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    commit = (subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
              if options.source_mode == "git" else None)
    sys.path.insert(0, str(root))
    from scripts.test_provenance import snapshot_sources, compare_sources, evidence_exit_code
    if options.source_mode == "filesystem":
        from scripts.test_provenance import snapshot_filesystem_sources
        snapshot_sources = snapshot_filesystem_sources
    source_start = snapshot_sources(root)
    (output / "source-start.json").write_text(
        json.dumps(source_start, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # 不继承宿主机业务配置；从类声明识别字段，避免手工漏掉新的密钥/服务配置。
    tree = ast.parse((root / "app/config/config.py").read_text(encoding="utf-8-sig"))
    fields = [node.target.id for cls in tree.body if isinstance(cls, ast.ClassDef)
              and cls.name == "Settings" for node in cls.body
              if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)]
    for key in [*fields, "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_TRACING_V2"]:
        os.environ.pop(key, None)
    sys.path.insert(0, str(root))
    original_directory = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="aicshub-t0-") as temporary:
        # Settings 的相对 .env 和运行时文件都落在空目录，不读取项目 .env。
        os.chdir(temporary)
        config = {
            "DATABASE_URL_OVERRIDE": f"sqlite+aiosqlite:///{Path(temporary).as_posix()}/offline.db",
            "REDIS_ENABLED": "false", "USE_MILVUS": "false",
            "LANGFUSE_ENABLED": "false", "USER_MEMORY_ENABLED": "false",
            "AUTH_DEMO_ACCOUNTS_ENABLED": "false", "LLM_API_KEY": "",
            "LLM_API_BASE": "http://offline.invalid/v1",
        }
        profile_config = {}
        if options.profile == "active":
            from scripts.offline_profiles import ACTIVE_CONFIG
            profile_config = dict(ACTIVE_CONFIG)
            config.update(profile_config)
        os.environ.update(config)
        blocked_connections: list[str] = []
        internal_pair = threading.local()
        original_socketpair = socket.socketpair

        def socketpair(*args, **kwargs):
            # Windows 的 asyncio 自唤醒管道使用标准库 socketpair 的回环实现。
            # 仅放行该同步函数内部创建的配对连接，不放行任意 localhost 连接。
            internal_pair.active = True
            try:
                return original_socketpair(*args, **kwargs)
            finally:
                internal_pair.active = False

        socket.socketpair = socketpair

        def block_network(event: str, args: tuple) -> None:
            if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"} and not getattr(
                internal_pair, "active", False
            ):
                blocked_connections.append(event)
                raise RuntimeError("T0 offline runner blocked a network operation")

        # 审计钩子也覆盖异步连接和工作线程；不记录目标地址或凭据。
        sys.addaudithook(block_network)
        # The process-local marker is not inherited by an unrelated pytest child.
        sys._aicshub_offline_guard = True
        import pytest

        if test_arguments:
            arguments = test_arguments
        elif options.profile == "active":
            from scripts.offline_profiles import ACTIVE_TESTS
            arguments = ["-q", *ACTIVE_TESTS]
        else:
            arguments = ["-q", "tests"]
        arguments = [str(root / arg) if (root / arg.split("::")[0]).exists()
                     and not arg.startswith("-") else arg for arg in arguments]
        manifest = {
            "profile": options.profile, "profile_config": profile_config,
            "source_mode": options.source_mode, "image_id": options.image_id,
            "commit": commit, "python": sys.version.split()[0], "environment": "T0",
            "dependencies": {name: version(name) for name in (
                "pytest", "pytest-asyncio", "SQLAlchemy", "aiosqlite", "fastapi", "langgraph",
                "langchain-core", "langchain-openai", "openai", "httpx", "pydantic",
            )},
            "arguments": arguments, "network_allowed": False,
            "config_fingerprint": hashlib.sha256(json.dumps(
                {**config, "DATABASE_URL_OVERRIDE": "temporary-sqlite"}, sort_keys=True
            ).encode()).hexdigest(),
        }
        try:
            with (output / "pytest.log").open("w", encoding="utf-8") as log:
                with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
                    code = int(pytest.main(["-c", str(root / "pytest.ini"),
                                           f"--basetemp={Path(temporary) / 'pytest'}",
                                           f"--junitxml={output / 'junit.xml'}", *arguments]))
            source_end = snapshot_sources(root)
            comparison = compare_sources(source_start, source_end)
            (output / "source-end.json").write_text(
                json.dumps(source_end, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            evidence_code = evidence_exit_code(code, comparison)
            if evidence_code == 0 and blocked_connections:
                evidence_code = 2
            manifest.update(
                pytest_exit_code=code, exit_code=evidence_code, **comparison,
                commit_at_end=subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=root, text=True
                ).strip() if options.source_mode == "git" else None,
                dirty_input_paths_at_start=source_start["dirty_input_paths"],
                blocked_network_operations=len(blocked_connections),
            )
            (output / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"T0 evidence: {output}")
            if evidence_code != code:
                print("T0 evidence rejected: source inputs changed or snapshot incomplete; see manifest.json")
            return evidence_code
        finally:
            os.chdir(original_directory)


if __name__ == "__main__":
    raise SystemExit(main())
