"""T0 离线回归入口：临时工作目录、独立 SQLite、禁止网络连接。

运行：python scripts/test_offline.py [-q] [tests/test_refund_flow.py ...]
完整输出、JUnit 和配置指纹保存在被 Git 忽略的 reports/offline/ 下。
此入口不是 T1 真实依赖或完整验收执行器。
"""
from __future__ import annotations

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
    root = Path(__file__).resolve().parents[1]
    output = root / "reports" / "offline" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    # 不继承宿主机业务配置；从类声明识别字段，避免手工漏掉新的密钥/服务配置。
    tree = ast.parse((root / "app/config/config.py").read_text(encoding="utf-8-sig"))
    fields = [node.target.id for cls in tree.body if isinstance(cls, ast.ClassDef)
              and cls.name == "Settings" for node in cls.body
              if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)]
    for key in fields:
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
        import pytest

        arguments = sys.argv[1:] or ["-q", "tests"]
        arguments = [str(root / arg) if (root / arg.split("::")[0]).exists()
                     and not arg.startswith("-") else arg for arg in arguments]
        manifest = {
            "commit": commit, "python": sys.version.split()[0], "environment": "T0",
            "dependencies": {name: version(name) for name in (
                "pytest", "pytest-asyncio", "SQLAlchemy", "aiosqlite", "fastapi", "langgraph"
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
            manifest.update(exit_code=code, blocked_network_operations=len(blocked_connections))
            (output / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"T0 evidence: {output}")
            return code
        finally:
            os.chdir(original_directory)


if __name__ == "__main__":
    raise SystemExit(main())
