"""One command: isolated active-path checks for workspace and immutable deployed image."""
from __future__ import annotations

from datetime import datetime
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def command(args, **kwargs):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=CHILD_ENV, **kwargs)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(args[:3]))
    return result.stdout


def source_hashes(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*.py") if "__pycache__" not in p.parts}


def deployed_snapshot():
    image = None
    sources = None
    services = {}
    code = "from pathlib import Path; import hashlib,json; print(json.dumps({p.relative_to('app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('app').rglob('*.py')}))"
    for service in ("app", "worker"):
        cid = command(["docker", "compose", "ps", "-q", service]).strip()
        if not cid:
            raise RuntimeError("Missing deployed service: " + service)
        actual_image = command(["docker", "inspect", "--format", "{{.Image}}", cid]).strip()
        actual_sources = json.loads(command(["docker", "exec", cid, "python", "-c", code]))
        if image is not None and (actual_image != image or sources != actual_sources):
            raise RuntimeError("API/Worker image or source mismatch")
        image, sources = actual_image, actual_sources
        services[service] = {"container": cid, "image": actual_image}
    return {"image": image, "app_sources": sources, "services": services}


def manifest_at(output, console):
    pointers = [line.split("T0 evidence: ", 1)[1].strip() for line in console.splitlines()
                if line.startswith("T0 evidence: ")]
    if len(pointers) != 1:
        raise RuntimeError("Offline evidence pointer missing")
    path = Path(pointers[0])
    if path.as_posix().startswith("/verification/"):
        path = output / "bundle" / path.relative_to("/verification")
    return path / "manifest.json"


def main():
    output = ROOT / "reports/offline-release" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    result = {"status": "RUNNING", "paid_provider_calls": 0, "scope": "active_synthetic_regression"}
    def save():
        (output / "closeout.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    try:
        before = deployed_snapshot()
        workspace = source_hashes(ROOT / "app")
        result["deployed"] = before
        result["workspace_app_sources"] = workspace
        result["workspace_vs_deployed"] = {
            "different_paths": sorted("app/" + k for k in workspace.keys() | before["app_sources"].keys()
                                      if workspace.get(k) != before["app_sources"].get(k)),
            "same_source": workspace == before["app_sources"],
        }
        run = subprocess.run([sys.executable, "scripts/test_offline.py", "--profile", "active"],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=CHILD_ENV)
        (output / "workspace.log").write_text(run.stdout + run.stderr, encoding="utf-8")
        path = manifest_at(output, run.stdout)
        workspace_report = json.loads(path.read_text(encoding="utf-8"))
        result["workspace_manifest"] = str(path.relative_to(ROOT))
        if run.returncode or workspace_report["exit_code"] or workspace_report["blocked_network_operations"]:
            raise RuntimeError("Workspace active regression failed; see workspace.log")
        save()
        bundle = output / "bundle"
        bundle.mkdir()
        for directory in ("scripts", "tests", "acceptance", "docs"):
            shutil.copytree(ROOT / directory, bundle / directory,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".env.*"))
        for filename in ("pytest.ini", "requirements.txt", "requirements.lock"):
            shutil.copy2(ROOT / filename, bundle / filename)
        (bundle / "expected-app.json").write_text(json.dumps(before["app_sources"]), encoding="utf-8")
        wheels = output / "wheels"
        wheels.mkdir()
        dependency = "aiosqlite==" + version("aiosqlite")
        # No dependency download during verification. Prepare the wheel cache explicitly.
        wheel_name = "aiosqlite-" + version("aiosqlite") + "-py3-none-any.whl"
        cached = ROOT / "reports/offline-wheel-cache" / wheel_name
        if not cached.is_file():
            raise RuntimeError("Test wheel missing; explicitly prepare reports/offline-wheel-cache/" + wheel_name)
        shutil.copy2(cached, wheels / wheel_name)
        bootstrap = """import hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links=/wheels',%r],check=True)
root=Path('/verification')
shutil.copytree('/app/app',root/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copytree('/app/alembic',root/'alembic',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
for name in ('requirements.txt','requirements.lock','alembic.ini'):
    if Path('/app',name).is_file(): shutil.copy2(Path('/app',name),root/name)
actual={p.relative_to(root/'app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'app').rglob('*.py')}
if actual != json.loads((root/'expected-app.json').read_text()): raise RuntimeError('deployed_source_copy_mismatch')
os.execv(sys.executable,[sys.executable,str(root/'scripts/test_offline.py'),'--profile','active','--source-mode','filesystem','--image-id',%r])
""" % (dependency, before["image"])
        cmd = ["docker", "run", "--rm", "--network", "none", "--user", "root", "--workdir", "/tmp",
               "--entrypoint", "python", "--mount", f"type=bind,source={bundle},target=/verification",
               "--mount", f"type=bind,source={wheels},target=/wheels,readonly",
               before["image"], "-c", bootstrap]
        run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=CHILD_ENV)
        (output / "deployed.log").write_text(run.stdout + run.stderr, encoding="utf-8")
        path = manifest_at(output, run.stdout)
        image_report = json.loads(path.read_text(encoding="utf-8"))
        result["deployed_manifest"] = str(path.relative_to(ROOT))
        result["test_wheels"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in wheels.glob("*.whl")}
        if run.returncode or image_report["exit_code"] or image_report["blocked_network_operations"]:
            raise RuntimeError("Deployed image active regression failed; see deployed.log")
        if deployed_snapshot() != before or source_hashes(ROOT / "app") != workspace:
            raise RuntimeError("Deployed or workspace app changed during verification")
        result.update(status="PASSED", source_stable=True, runtime_unchanged=True,
                      coverage_note="Model/task/item-v2 configuration with synthetic providers; not semantic gold or paid E2E")
        save()
        print("Offline release evidence: " + str(output))
        return 0
    except Exception as exc:
        result.update(status="FAILED", error=str(exc))
        save()
        print(str(exc))
        print("Offline release evidence: " + str(output))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
