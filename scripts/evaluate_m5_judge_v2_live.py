"""Judge v2固定候选实测准备/替身演练；真实请求必须另有绑定预算的新授权。"""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "acceptance/m5/judge-v2-live-v1"
PLAN = ROOT / "docs/M5-JUDGE-V2-LIVE-PLAN-v1.json"
RUN = ROOT / "reports/m5-judge-v2-live"


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_dataset() -> dict:
    data = json.loads((DATA / "dataset.json").read_bytes())
    manifest = json.loads((DATA / "manifest.json").read_bytes())
    if sha(DATA / "dataset.json") != manifest["dataset_sha256"]:
        raise ValueError("frozen_dataset_hash_changed")
    for relative, expected in manifest["source_hashes"].items():
        if sha(ROOT / relative) != expected:
            raise ValueError("old_diagnostic_material_changed")
    keys = [case["case_id"] for case in data["cases"]]
    if len(keys) != 256 or len(set(keys)) != 256 or data["counts"] != manifest["counts"]:
        raise ValueError("frozen_dataset_count_changed")
    return data


def build_plan() -> dict:
    from scripts.evaluate_m5_live import public_config, digest
    from scripts.test_provenance import snapshot_sources
    from app.agent.judge_contracts import JUDGE_V2_PROMPT, JudgeVerdictV2
    dataset = load_dataset()
    provider = public_config()
    if provider.get("LLM_STRUCTURED_OUTPUT_METHOD") != "function_calling":
        raise ValueError("this_plan_requires_function_calling")
    protected = [ROOT / "reports/m4-closeout/live/authorization-ledger.json", ROOT / "reports/m5-live/authorization.json",
                 ROOT / "reports/m5-live/dispatch.sqlite", ROOT / "reports/m5-live/current/report.json"]
    value = {"schema_version": 1, "status": "PROPOSED_NOT_AUTHORIZED", "provider": provider,
        "judge_contract": "v2", "source_fingerprint": snapshot_sources(ROOT)["fingerprint"],
        "judge_policy_sha256": digest([JUDGE_V2_PROMPT, JudgeVerdictV2.model_json_schema()]),
        "input_hashes": {path.relative_to(ROOT).as_posix(): sha(path) for path in [DATA / "dataset.json", DATA / "manifest.json"]},
        "protected_prior_hashes": {path.relative_to(ROOT).as_posix(): sha(path) for path in protected},
        "proposed_limits": {"max_requests": 256, "max_input_tokens": 4000000, "max_output_tokens": 262144},
        "operations": {case["case_id"] + ":judge": 1024 for case in dataset["cases"]},
        "counts": dataset["counts"], "new_input_clusters": 25, "independent_human_labels": False,
        "phase_order": ["smoke", "known", "new_negative", "new_positive", "boundary"],
        "stop_on_smoke_unknown_or_wrong": True, "automatic_retry": False, "regeneration": False,
        "parallel_requests": 1, "per_request_output_cap": 1024, "max_http_input_bytes": 24576,
        "whole_m5_pass": False, "price_verified": False, "invoice_cost_cap": None,
        "limits": ["input reservation uses full HTTP JSON UTF-8 bytes as conservative token upper bound",
                   "fixed candidates only; no fresh generation, full Agent, retrieval ablation or production deployment",
                   "new25-cluster assistant-authored diagnostics are not independent human holdout",
                   "six smoke cases must pass before paid bulk; failures remain and are not retried",
                   "business .env and default Judge v1 remain unchanged"]}
    value["plan_id"] = digest(value)
    return value


def authorization(plan: dict) -> dict:
    from scripts.m5_live_budget import validate_authorization
    if not PLAN.is_file() or json.loads(PLAN.read_bytes()) != plan:
        raise ValueError("prepared_plan_changed")
    path = RUN / "authorization.json"
    if not path.exists():
        raise ValueError("explicit_new_request_and_token_limits_required")
    return validate_authorization(json.loads(path.read_bytes()), plan)


def mock_payload(case: dict) -> dict:
    """带显式标签的协议夹具，只有替身演练使用，不能作为质量结果。"""
    from app.agent.judge_contracts import response_segments, evidence_catalog
    raw = {"schema_version": 2, "answerability": "full", "response_behavior": "answer",
        "response_validity": "valid" if case["expected_allowed"] else "invalid", "claims_complete": True,
        "claims": [], "nonfact_segments": [], "response_issues": [] if case["expected_allowed"] else ["unsupported_detail"]}
    catalog = evidence_catalog(case["evidence"], case["tool_evidence"])
    safe_unknown = case["case_id"] == "smoke-unknown" or case["category"] == "safe_unknown"
    partial = case["case_id"] == "smoke-condition" or case["category"] == "safe_partial"
    if safe_unknown:
        raw.update(answerability="unknown", response_behavior="clarify")
    if partial:
        raw.update(answerability="partial", response_behavior="answer_with_limits")
    for segment in response_segments(case["candidate"]):
        if safe_unknown or (partial and segment["segment_id"] > 0):
            raw["nonfact_segments"].append({"segment_id": segment["segment_id"], "kind": "clarification"})
            continue
        references = []
        if catalog:
            key, body = next(iter(catalog.items()))
            references = [{"kind": key[0], "source_id": key[1], "version_id": key[2], "quote": body}]
        raw["claims"].append({"segment_id": segment["segment_id"], "text": segment["text"],
            "support": "supported" if case["expected_allowed"] else "unsupported", "evidence": references,
            "issues": [] if case["expected_allowed"] else ["unsupported_detail"]})
    return raw


async def pipeline(plan: dict, output: Path, limits: dict, dry: bool) -> dict:
    import httpx
    from openai import AsyncOpenAI
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.agent.validation_llm import LLMRAGJudge
    from app.agent.validation import ResponseValidator
    from app.agent.publication_guard import publication_issues
    from app.agent.citation_guard import citation_issues
    from app.models.models import Base
    from app.config.config import settings
    from app.services.model_gateway import model_context
    from app.services.llm_service import LLMService
    from scripts.m5_live_budget import DispatchLedger, OPERATION, guarded_transport
    if settings.LLM_STRUCTURED_OUTPUT_METHOD != "function_calling":
        raise ValueError("this_plan_requires_function_calling")
    dataset = load_dataset()
    by_operation = {case["case_id"] + ":judge": case for case in dataset["cases"]}
    # dry-run账本留在临时目录，绝不能初始化真实额度。
    ledger_path = Path.cwd() / "mock.sqlite" if dry else RUN / "dispatch.sqlite"
    ledger = DispatchLedger(ledger_path, plan["plan_id"], limits, plan["operations"])
    async with AsyncExitStack() as resources:
        engine = create_async_engine("sqlite+aiosqlite:///" + (ledger_path.parent / "gateway.db").resolve().as_posix(), poolclass=NullPool)
        resources.push_async_callback(engine.dispose)
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        def respond(request):
            payload = mock_payload(by_operation[OPERATION.get()])
            return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
                "model": settings.LLM_MODEL, "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{"id": "synthetic-call", "type": "function",
                    "function": {"name": "JudgeVerdictV2", "arguments": json.dumps(payload, ensure_ascii=False)}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
        client = httpx.AsyncClient(transport=guarded_transport(ledger, model=settings.LLM_MODEL, endpoint=settings.LLM_API_BASE,
            underlying=httpx.MockTransport(respond) if dry else None), timeout=30, trust_env=False, follow_redirects=False)
        resources.push_async_callback(client.aclose)
        service = LLMService()
        resources.push_async_callback(service.close)
        service.RETRY_DELAY = 0
        original = service._get_chat_model
        def model(config, **kwargs):
            result = original(config, **kwargs)
            sdk = AsyncOpenAI(api_key=config.api_key, base_url=config.api_base, http_client=client, max_retries=0)
            result.root_async_client = sdk
            result.async_client = sdk.chat.completions
            return result
        service._get_chat_model = model
        judge, rows = LLMRAGJudge(service), []
        status = "MOCK_REHEARSAL_NOT_QUALITY" if dry else "REAL_DIAGNOSTIC_REVIEW_REQUIRED"
        smoke_failed = False
        for case in dataset["cases"]:
            # 下一次付费派发前复核源码；本轮记录仍保留，不重置已用额度。
            expected_source = plan.get("source_fingerprint")
            if expected_source:
                from scripts.test_provenance import snapshot_sources
                current_source = snapshot_sources(ROOT)
                if current_source["fingerprint"] != expected_source or current_source["unsupported_paths"]:
                    status = "STOPPED_SOURCE_DRIFT_BEFORE_NEXT_DISPATCH"
                    break
            if case["phase"] != "smoke" and smoke_failed:
                status = "STOPPED_SMOKE_FAILED"
                break
            operation = case["case_id"] + ":judge"
            path = output / "cases" / (hashlib.sha256(case["case_id"].encode()).hexdigest() + ".json")
            if path.exists():
                row = json.loads(path.read_bytes())
            elif operation in ledger.dispatched():
                row = {"case_id": case["case_id"], "phase": case["phase"], "schema_valid": False,
                    "judge_allowed": False, "combined_allowed": False, "reason": "prior_dispatch_output_unknown_no_retry"}
            else:
                if ledger.snapshot()["requests"] >= limits["max_requests"]:
                    status = "STOPPED_AUTHORIZED_REQUEST_LIMIT"
                    break
                began = time.perf_counter()
                token = OPERATION.set(operation)
                try:
                    async with model_context(990006, "judge-v2:" + case["case_id"], factory=factory):
                        result = await judge.judge(case["candidate"], case["question"], case["evidence"], case["tool_evidence"])
                finally:
                    OPERATION.reset(token)
                receipts = [{"success": True, "data": json.loads(line)} for line in case["tool_evidence"].splitlines() if line.strip()]
                validation = ResponseValidator().validate(case["candidate"], case["question"], "general", receipts)
                issues = citation_issues(case["candidate"], case["evidence"]) + publication_issues(case["candidate"])
                row = {"case_id": case["case_id"], "phase": case["phase"], "category": case["category"],
                    "label_source": case["label_source"], "expected_allowed": case["expected_allowed"],
                    "schema_valid": result.get("schema_version") == 2, "judge_allowed": bool(result["publish_allowed"]),
                    "combined_allowed": bool(result["publish_allowed"] and validation.passed and not issues),
                    "judgement": {key: value for key, value in result.items() if key != "_v2_verdict"},
                    "program_issue_codes": sorted(set(issues + [item["type"] for item in validation.issues])),
                    "latency_ms": (time.perf_counter() - began) * 1000, "business_actions_executed": 0}
            save(path, row)
            rows.append(row)
            if case["phase"] == "smoke" and (not row["schema_valid"] or row["judge_allowed"] != case["expected_allowed"]):
                smoke_failed = True
        return {"status": status, "plan_id": plan["plan_id"], "rows": rows, "evaluated_cases": len(rows),
            "ledger": ledger.snapshot(), "external_model_calls": 0 if dry else ledger.snapshot()["requests"],
            "labels_are_independent_human": False, "whole_m5_pass": False, "business_default_changed": False}


def worker(plan: dict, output: Path, dry: bool) -> dict:
    limits = plan["proposed_limits"] if dry else authorization(plan)
    if not dry and output.resolve() != (RUN / "current").resolve():
        raise ValueError("fixed_output_required")
    from dotenv import dotenv_values
    key = "synthetic-key" if dry else dotenv_values(ROOT / ".env").get("LLM_API_KEY")
    if not key:
        raise ValueError("credential_missing")
    tree = ast.parse((ROOT / "app/config/config.py").read_text(encoding="utf-8-sig"))
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == "Settings":
            for field in cls.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    os.environ.pop(field.target.id, None)
    os.environ.update(plan["provider"], LLM_API_KEY=key, LLM_JUDGE_CONTRACT_VERSION="v2", LLM_JUDGE_ENABLED="true",
        LLM_GATEWAY_ENABLED="true", LLM_MAX_RETRIES="0", LLM_MAX_TOKENS="1024", LLM_MAX_CONCURRENCY="1",
        LLM_DAILY_USER_TOKEN_BUDGET="10000000", LLM_DAILY_USER_COST_USD="100", LLM_MAX_COST_USD="1",
        REDIS_ENABLED="false", USE_MILVUS="false", LANGFUSE_ENABLED="false", USER_MEMORY_ENABLED="false")
    if dry:
        local = threading.local()
        original = socket.socketpair
        def pair(*args, **kwargs):
            local.pair = True
            try:
                return original(*args, **kwargs)
            finally:
                local.pair = False
        socket.socketpair = pair
        def audit(event, args):
            if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"} and not getattr(local, "pair", False):
                raise RuntimeError("mock_rehearsal_network_forbidden")
        sys.addaudithook(audit)
    return asyncio.run(pipeline(plan, output, limits, dry))


def main() -> int:
    from scripts.evaluate_m5_live import exclusive_run
    from scripts.test_provenance import snapshot_sources, compare_sources
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--execute", action="store_true")
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    plan = build_plan()
    if args.worker_output:
        with exclusive_run((args.worker_output if args.dry_run else RUN) / "execution.lock"):
            result = worker(plan, args.worker_output, args.dry_run)
            save(args.worker_output / "report.json", result)
        return 0
    if args.prepare or not (args.dry_run or args.execute):
        save(PLAN, plan)
        print(json.dumps({"status": plan["status"], "plan_id": plan["plan_id"], "limits": plan["proposed_limits"]}))
        return 0
    if args.execute:
        authorization(plan)
    before = snapshot_sources(ROOT)
    output = RUN / "current" if args.execute else ROOT / "reports/m5" / datetime.now().strftime("judge-v2-live-rehearsal-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=True)
    code, failure = None, None
    try:
        with tempfile.TemporaryDirectory(prefix="aicshub-jv2-eval-") as temporary:
            with (output / "worker.log").open("a", encoding="utf-8") as log:
                code = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--execute" if args.execute else "--dry-run",
                    "--worker-output", str(output)], cwd=temporary, stdout=log, stderr=subprocess.STDOUT, timeout=3600).returncode
    except Exception as exc:
        failure = type(exc).__name__
    result = json.loads((output / "report.json").read_bytes()) if (output / "report.json").exists() else {"status": "FAIL_NO_REPORT", "whole_m5_pass": False}
    after = snapshot_sources(ROOT)
    protected_stable = all(sha(ROOT / relative) == value for relative, value in plan["protected_prior_hashes"].items())
    result.update(compare_sources(before, after), prior_ledgers_stable=protected_stable, worker_exit_code=code)
    if code != 0 or failure or not result["source_tree_stable"] or not result["source_snapshot_complete"] or not protected_stable:
        result.update(status="FAIL_SOURCE_OR_WORKER", error_type=failure or "worker_or_protected_source_error")
    if args.dry_run and result.get("evaluated_cases") != 256:
        result["status"] = "FAIL_INCOMPLETE_MOCK_REHEARSAL"
    for name, value in (("report", result), ("source-start", before), ("source-end", after)):
        save(output / f"{name}.json", value)
    print(json.dumps({"status": result["status"], "report": str(output / "report.json")}))
    return 2 if result["status"].startswith("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
