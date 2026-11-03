"""独立MySQL/Redis验收入口；仅接受本次test.owner标记容器。"""

import ast
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import json, os, subprocess, sys, tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from scripts.test_offline import Tee

    runtime = root / "reports/m3-closeout/runtime/state.json"
    state = json.loads(runtime.read_text(encoding="utf-8"))
    if not state["prefix"].startswith("aicshub-m3-acceptance-"):
        raise RuntimeError("Refuse unowned test resources")
    for cid in state["containers"].values():
        label = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "aicshub.test.owner"}}',
                cid,
            ],
            text=True,
        ).strip()
        if label != "m3-closeout":
            raise RuntimeError("Refuse non-test container")
    tree = ast.parse((root / "app/config/config.py").read_text(encoding="utf-8-sig"))
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == "Settings":
            for node in cls.body:
                if isinstance(node, ast.AnnAssign) and isinstance(
                    node.target, ast.Name
                ):
                    os.environ.pop(node.target.id, None)
    secret = state["secret"]
    os.environ.update(
        DATABASE_URL_OVERRIDE=f"mysql+aiomysql://root:{secret}@127.0.0.1:{state['ports']['mysql']}/aicshub_m3_acceptance",
        REDIS_ENABLED="true",
        REDIS_HOST="127.0.0.1",
        REDIS_PORT=state["ports"]["redis"],
        REDIS_DB="0",
        USE_MILVUS="true",
        MILVUS_HOST="127.0.0.1",
        MILVUS_PORT=state["ports"]["milvus"],
        EMBEDDING_API_BASE="",
        EMBEDDING_API_KEY="",
        EMBEDDING_MODEL="synthetic-m3",
        EMBEDDING_DIM="3",
        LANGFUSE_ENABLED="false",
        USER_MEMORY_ENABLED="false",
        AUTH_DEMO_ACCOUNTS_ENABLED="false",
        AUTH_SECRET_KEY="synthetic-m3-secret-key-only-for-isolated-tests",
        LLM_API_KEY="",
        DEBUG="false",
        M3_TEST_STATE=str(runtime),
    )
    output = (
        root / "reports/m3-closeout" / datetime.now().strftime("real-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True)
    old = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="aicshub-real-") as temp:
        os.chdir(temp)
        import pytest

        with (output / "pytest.log").open("w", encoding="utf-8") as log:
            with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(
                Tee(sys.stderr, log)
            ):
                code = int(
                    pytest.main(
                        [
                            "-q",
                            "--tb=short",
                            "-c",
                            str(root / "pytest.ini"),
                            f"--junitxml={output/'junit.xml'}",
                            *(
                                [str(root / "acceptance/m3/real_services.py")]
                                + sys.argv[1:]
                            ),
                        ]
                    )
                )
        (output / "manifest.json").write_text(
            json.dumps(
                {
                    "environment": "T1",
                    "exit_code": code,
                    "commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=root, text=True
                    ).strip(),
                    "containers": state["containers"],
                    "ports": state["ports"],
                    "production_resources_used": False,
                }
            ),
            encoding="utf-8",
        )
        os.chdir(old)
    print("T1 evidence:", output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
