"""独立MySQL/Redis验收入口；仅接受本次test.owner标记容器。"""
import ast
import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import json, os, subprocess, sys, tempfile
from pathlib import Path


def verified_runtime(root: Path, *, reuse_m3: bool = False):
    scope = "m3" if reuse_m3 else "m1"
    runtime = root / f"reports/{scope}-closeout/runtime/state.json"
    state = json.loads(runtime.read_text(encoding="utf-8"))
    if not state["prefix"].startswith(f"aicshub-{scope}-acceptance-"):
        raise RuntimeError("Refuse unowned test resources")
    for role, cid in state["containers"].items():
        details = subprocess.check_output([
            "docker", "inspect", "--format",
            '{{index .Config.Labels "aicshub.test.owner"}}|{{.State.Status}}|{{json .NetworkSettings.Ports}}', cid,
        ], text=True).strip()
        owner, status, raw_ports = details.split("|", 2)
        if owner != f"{scope}-closeout":
            raise RuntimeError("Refuse non-test container")
        if role in {"mysql", "redis"}:
            if status != "running":
                raise RuntimeError("Required isolated test service is not running")
            bindings = (json.loads(raw_ports) or {}).get("3306/tcp" if role == "mysql" else "6379/tcp") or []
            if not any(binding.get("HostIp") == "127.0.0.1"
                       and binding.get("HostPort") == str(state["ports"][role]) for binding in bindings):
                raise RuntimeError("Isolated test port does not match verified container")
    if not {"mysql", "redis"}.issubset(state["containers"]):
        raise RuntimeError("Required isolated services missing")
    return runtime, state, f"{scope}-closeout"


def main():
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    from scripts.test_offline import Tee
    from scripts.test_provenance import snapshot_sources, compare_sources, evidence_exit_code
    source_start = snapshot_sources(root)
    start_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--reuse-m3", action="store_true")
    options, test_arguments = parser.parse_known_args()
    runtime, state, test_owner = verified_runtime(root, reuse_m3=options.reuse_m3)
    tree=ast.parse((root/"app/config/config.py").read_text(encoding="utf-8-sig"))
    for cls in tree.body:
        if isinstance(cls,ast.ClassDef) and cls.name=="Settings":
            for node in cls.body:
                if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name):
                    os.environ.pop(node.target.id,None)
    secret=state["secret"]
    base_database = "aicshub_m3_acceptance" if options.reuse_m3 else "aicshub_m1_acceptance"
    os.environ.update(DATABASE_URL_OVERRIDE=f"mysql+aiomysql://root:{secret}@127.0.0.1:{state['ports']['mysql']}/{base_database}",
        REDIS_ENABLED="true",REDIS_HOST="127.0.0.1",REDIS_PORT=state["ports"]["redis"],REDIS_DB="0",
        USE_MILVUS="false",LANGFUSE_ENABLED="false",USER_MEMORY_ENABLED="false",AUTH_DEMO_ACCOUNTS_ENABLED="false",
        AUTH_SECRET_KEY="synthetic-m1-secret-key-only-for-isolated-tests",LLM_API_KEY="",DEBUG="false",M1_TEST_STATE=str(runtime))
    output=root/"reports/m1-closeout"/datetime.now().strftime("real-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    (output / "source-start.json").write_text(json.dumps(source_start, indent=2), encoding="utf-8")
    old=Path.cwd()
    with tempfile.TemporaryDirectory(prefix="aicshub-real-") as temp:
        os.chdir(temp)
        import pytest
        with (output/"pytest.log").open("w",encoding="utf-8") as log:
            with redirect_stdout(Tee(sys.stdout,log)),redirect_stderr(Tee(sys.stderr,log)):
                code=int(pytest.main(["-q","--tb=short","-c",str(root/"pytest.ini"),
                    f"--junitxml={output/'junit.xml'}",*([str(root/'acceptance/m1/real_services.py')]+test_arguments)]))
        source_end = snapshot_sources(root)
        comparison = compare_sources(source_start, source_end)
        evidence_code = evidence_exit_code(code, comparison)
        (output / "source-end.json").write_text(json.dumps(source_end, indent=2), encoding="utf-8")
        (output/"manifest.json").write_text(json.dumps({"environment":"T1","exit_code":evidence_code,
            "pytest_exit_code":code, **comparison, "dirty_input_paths_at_start":source_start["dirty_input_paths"],
            "commit":start_commit,"commit_at_end":subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
            "containers":state["containers"],"ports":state["ports"],"production_resources_used":False,
            "verified_test_owner":test_owner,"resource_runtime_path":str(runtime),
            "runtime_reused":options.reuse_m3,"base_database":base_database}),encoding="utf-8")
        os.chdir(old)
    print("T1 evidence:",output)
    return evidence_code

if __name__=="__main__":
    raise SystemExit(main())
