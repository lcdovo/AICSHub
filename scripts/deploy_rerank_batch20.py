"""Prepare an isolated batch20 image from the deployed immutable base. No provider chat calls."""
from pathlib import Path
import difflib
import hashlib
import json
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/rerank-batch20-rollout-20261108"
TAG = "ai-customer:rerank-batch20-20261108"

def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8", **kwargs).stdout

def prepare():
    OUT.mkdir(parents=True, exist_ok=False)
    private = OUT / "private"
    private.mkdir()
    ids = {s:run(["docker","compose","ps","-q",s]).strip() for s in ("app","worker")}
    info = {s:json.loads(run(["docker","inspect",i]))[0] for s,i in ids.items()}
    base = info["app"]["Image"]
    if base != info["worker"]["Image"]:
        raise RuntimeError("api_worker_base_image_mismatch")
    run(["docker","tag",base,"ai-customer:before-rerank-batch20-20261108"])
    (private/"runtime.json").write_text(json.dumps(info),encoding="utf-8")
    context = OUT / "context"
    context.mkdir()
    run(["docker","cp",ids["app"]+":/app/app",str(context/"app")])
    files = ["app/config/config.py","app/services/reranker_service.py","app/services/knowledge_base/versioned_search.py"]
    before = {f:(context/f).read_text(encoding="utf-8") for f in files}
    p=context/files[0]; s=before[files[0]]
    if "RERANKER_LOCAL_BATCH_SIZE:" in s: raise RuntimeError("base_already_configurable")
    s=s.replace('    RERANKER_LOCAL_MODEL:', '    RERANKER_LOCAL_BATCH_SIZE: int = Field(default=4, ge=1, le=24)\n    RERANKER_LOCAL_MODEL:',1)
    p.write_text(s,encoding="utf-8")
    p=context/files[1];s=before[files[1]]
    s=s.replace('LOCAL_BATCH_SIZE = 4\n','')
    s=s.replace('local-bge-batch4-parallel2-v1','local-bge-configurable-batch-parallel2-v2')
    s=s.replace('                if provider.name == PROVIDER_LOCAL and provider.model == "BAAI/bge-reranker-large" and len(docs) > LOCAL_BATCH_SIZE:', '                from app.config.config import settings\n                if provider.name == PROVIDER_LOCAL and provider.model == "BAAI/bge-reranker-large" and len(docs) > settings.RERANKER_LOCAL_BATCH_SIZE:')
    s=s.replace('        pending = {}','        from app.config.config import settings\n        batch_size = settings.RERANKER_LOCAL_BATCH_SIZE\n        pending = {}',1)
    s=s.replace('offset + LOCAL_BATCH_SIZE','offset + batch_size')
    if 'LOCAL_BATCH_SIZE' in s.replace('RERANKER_LOCAL_BATCH_SIZE',''):raise RuntimeError("unpatched_batch_constant")
    p.write_text(s,encoding="utf-8")
    p=context/files[2];s=before[files[2]].replace('            "RERANKER_LOCAL_MODEL",','            "RERANKER_LOCAL_MODEL",\n            "RERANKER_LOCAL_BATCH_SIZE",',1)
    p.write_text(s,encoding="utf-8")
    diff=''.join(''.join(difflib.unified_diff(before[f].splitlines(True),(context/f).read_text(encoding="utf-8").splitlines(True),fromfile='base/'+f,tofile='candidate/'+f)) for f in files)
    (OUT/"candidate.patch").write_text(diff,encoding="utf-8")
    (context/"Dockerfile").write_text('FROM ai-customer:before-rerank-batch20-20261108\n'+''.join(f'COPY --chown=appuser:appuser {f} /app/{f}\n' for f in files),encoding="utf-8")
    overrides={"services":{}}
    rollback={"services":{}}
    for service in ("app","worker"):
        env=dict(item.split("=",1) for item in info[service]["Config"]["Env"])
        env["RERANKER_LOCAL_BATCH_SIZE"]="20"
        overrides["services"][service]={"image":TAG,"environment":env}
        old=dict(item.split("=",1) for item in info[service]["Config"]["Env"])
        old["RERANKER_LOCAL_BATCH_SIZE"]="4"
        rollback["services"][service]={"image":"ai-customer:before-rerank-batch20-20261108","environment":old}
    class ExactEnvironment(dict):
        pass
    yaml.SafeDumper.add_representer(ExactEnvironment,
        lambda dumper, value: dumper.represent_mapping("!override", value))
    for mode, value in (("candidate", overrides), ("rollback", rollback)):
        for service in ("app", "worker"):
            value["services"][service]["environment"] = ExactEnvironment(value["services"][service]["environment"])
        (private/(mode+"-exact.yml")).write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    manifest={"base_image":base,"candidate_tag":TAG,"changed_files":files,
        "files":{f:{"before":hashlib.sha256(before[f].encode()).hexdigest(),"after":hashlib.sha256((context/f).read_bytes()).hexdigest()} for f in files},
        "status":"PREPARED_NOT_DEPLOYED","provider_chat_calls":0}
    (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps(manifest))

if __name__=="__main__":prepare()
