"""M5单次生成/独立Judge诊断：默认只准备计划，真实派发必须有新的固定额度授权。"""
from __future__ import annotations

import argparse
import ast
import asyncio
from collections import Counter
from contextlib import AsyncExitStack, contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PLAN_PATH = ROOT / "docs/M5-LIVE-PLAN-v1.json"
LIVE_ROOT = ROOT / "reports/m5-live"
PUBLIC_FIELDS = ("LLM_API_BASE", "LLM_MODEL", "LLM_MODEL_VERSION", "LLM_THINKING_MODE", "LLM_STRUCTURED_OUTPUT_METHOD",
                 "LLM_INPUT_PRICE_PER_1K_USD", "LLM_OUTPUT_PRICE_PER_1K_USD", "LLM_PRICE_VERIFIED")


@contextmanager
def exclusive_run(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if sys.platform == "win32":
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def operations(dataset) -> dict[str, int]:
    result = {}
    for case in dataset.cases:
        if case.kind in {"answerable", "unanswerable"}:
            result[case.case_id + ":generation"] = 512
        result[case.case_id + ":judge"] = 256
    return result


def public_config() -> dict:
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / ".env")
    result = {key: values[key] for key in PUBLIC_FIELDS if values.get(key) is not None}
    if result["LLM_API_BASE"] != "https://token.uino.com/v1" or result["LLM_MODEL"] != "deepseek-flash":
        raise ValueError("provider_configuration_requires_new_plan")
    return result


def build_plan() -> dict:
    from scripts.evaluate_m5_offline_chain import load_package
    from scripts.test_provenance import snapshot_sources
    dataset, _, inputs = load_package(ROOT / "acceptance/m5/adjudicated-v2", ROOT / "acceptance/m5/development.json")
    old_path = ROOT / "reports/m4-closeout/live/authorization-ledger.json"
    old = json.loads(old_path.read_bytes())
    if old["request_count"] != 660 or old["max_requests"] != 660:
        raise ValueError("original_authorization_ledger_not_expected_exhausted_state")
    ops = operations(dataset)
    value = {"schema_version": 1, "status": "PROPOSED_NOT_AUTHORIZED", "provider": public_config(),
        "source_fingerprint": snapshot_sources(ROOT)["fingerprint"],
        "input_hashes": {Path(path).relative_to(ROOT).as_posix(): sha for path, sha in inputs.items()},
        "old_m4_ledger_sha256": hashlib.sha256(old_path.read_bytes()).hexdigest(),
        "old_m4_requests": 660, "counts": dict(Counter(c.kind for c in dataset.cases)),
        "proposed_limits": {"max_requests": len(ops), "max_input_tokens": 16000000, "max_output_tokens": sum(ops.values())},
        "max_input_utf8_bytes_per_http_request": 24576, "operations": ops,
        "phase_order": ["unanswerable", "review_negative", "review_positive", "answerable"],
        "mode": "bm25_cold_sql_single_pass_generation_and_judge",
        "scope": "production response generation helper and candidate validation; not full Agent/REST/WS or semantic ground truth",
        "automatic_retry": False, "regeneration": False, "parallel_requests": 1,
        "whole_m5_pass": False, "price_verified": False, "invoice_cost_cap": None,
        "limits": ["input reservation uses complete HTTP UTF-8 bytes as conservative token upper bound",
                   "all materials are already-seen assistant-reviewed synthetic diagnostics, not new independent holdout",
                   "output behavior, necessary-condition coverage and claim support require separate output review",
                   "performance remains deferred; no business tools, business database, cache or model route changes"]}
    value["plan_id"] = digest(value)
    return value


def require_authorization(plan: dict) -> dict:
    from scripts.m5_live_budget import validate_authorization
    if not PLAN_PATH.is_file() or json.loads(PLAN_PATH.read_bytes()) != plan:
        raise ValueError("prepared_plan_missing_or_changed")
    path = LIVE_ROOT / "authorization.json"
    if not path.is_file():
        raise ValueError("new_explicit_user_authorization_required")
    return validate_authorization(json.loads(path.read_bytes()), plan)


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def case_path(output: Path, case_id: str) -> Path:
    return output / "cases" / (hashlib.sha256(case_id.encode()).hexdigest() + ".json")


def mock_provider(request):
    """仅替身演练使用；返回固定合约值，不读取题目金标。"""
    import httpx
    payload = json.loads(request.content)
    name = payload["tools"][0]["function"]["name"]
    value = ({"reply": "现有资料不足以确认，请补充具体产品型号。", "intent": "general"} if name == "LLMResponse"
             else {"grounded": False, "fact_score": 0.0, "has_unsupported_claim": True,
                   "refuse_recommended": True, "reason": "synthetic transport rehearsal"})
    return httpx.Response(200, json={"id": "synthetic-response", "object": "chat.completion", "created": 1,
        "model": payload["model"], "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": "synthetic-call", "type": "function",
            "function": {"name": name, "arguments": json.dumps(value, ensure_ascii=False)}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})


async def evaluate_cases(dataset, kb, mapping: dict, service, node, judge, factory, ledger, output: Path) -> dict:
    from app.agent.state import AgentState
    from app.agent.request_understanding import EvidenceBundle
    from app.agent.nodes.response_node import validate_candidate
    from app.services.model_gateway import model_context
    from scripts.m5_live_budget import OPERATION
    rows = []
    reverse = {(v["id"], v["version_id"]): key for key, v in mapping.items()}
    ordered = sorted(dataset.cases, key=lambda case: ["unanswerable", "review_negative", "review_positive", "answerable"].index(case.kind))
    for case in ordered:
        path = case_path(output, case.case_id)
        record = json.loads(path.read_bytes()) if path.is_file() else {"case_id": case.case_id, "kind": case.kind}
        generated = case.kind in {"answerable", "unanswerable"}
        # 生成证据只由真实检索决定，不按相关标签/必要条件选中金标。
        if generated:
            kb._query_cache.clear()
            result = kb.search(case.query, top_k=20, recall_k=20, rerank_k=20, retrieval_mode="bm25")
            ranked = result["results"]
            evidence = ranked[:5]
        else:
            ranked = evidence = [mapping[ref.key()] for ref in case.relevant]
        canonical = lambda item: {"chunk_id": reverse[(item["id"], item["version_id"])][0],
                                  "version_id": reverse[(item["id"], item["version_id"])][1]}
        state = AgentState(user_id=990005, session_id="m5-live-synthetic", user_message=case.query,
                           evidence_bundle=EvidenceBundle(items=evidence))
        state.set_flow(rag_grounded=bool(evidence))
        for tool in case.tool_results:
            state.add_tool_call("synthetic_receipt", {}, tool, tool.get("success") is True)
        record.update(ranked_chunks=[canonical(item) for item in ranked], evidence_chunks=[canonical(item) for item in evidence],
                      business_actions_executed=0, claim_support=None, response_behavior=None, covered_conditions=None,
                      output_label_source="unreviewed", whole_m5_pass=False)
        # HTTP已派发但进程未保存输出的操作标unknown，不自动重发。
        if generated and "generation" not in record:
            operation = case.case_id + ":generation"
            reply = None
            if operation in ledger.dispatched():
                record["generation"] = {"status": "unknown_prior_dispatch_no_replay"}
            else:
                token = OPERATION.set(operation)
                try:
                    async with model_context(990005, "m5:" + operation, factory=factory):
                        reply = await node._generate_response_with_llm(state)
                    record["generation"] = {"status": "generated" if reply and not state.current_generation_metadata().degraded else "failed_or_fallback",
                        "reply": service.redactor.redact(reply or ""), "reply_sha256": hashlib.sha256((reply or "").encode()).hexdigest()}
                finally:
                    OPERATION.reset(token)
            save(path, record)
        # 恢复时仅使用原结果；若脱敏改变正文，不把修改后的正文当原模型稿复验。
        if generated:
            generation = record["generation"]
            candidate = generation.get("reply", "")
            valid = (generation.get("status") == "generated" and candidate
                     and hashlib.sha256(candidate.encode()).hexdigest() == generation.get("reply_sha256"))
            candidate = candidate if valid else ""
        else:
            candidate = case.candidate or ""
        operation = case.case_id + ":judge"
        if "judgement" not in record:
            if not candidate:
                record["judgement"] = {"support": "unknown", "reason": "candidate_missing_or_redacted_no_retry"}
            elif operation in ledger.dispatched():
                record["judgement"] = {"support": "unknown", "reason": "unknown_prior_dispatch_no_replay"}
            else:
                token = OPERATION.set(operation)
                try:
                    async with model_context(990005, "m5:" + operation, factory=factory):
                        record["judgement"] = await judge.judge(candidate, case.query, evidence, node._tool_evidence_text(state))
                finally:
                    OPERATION.reset(token)
            save(path, record)
        # 使用实际Judge结果回放发布候选闸门，不再额外收费或重新生成。
        class RecordedJudge:
            async def judge(self, **kwargs):
                return record["judgement"]
        from dataclasses import replace
        from app.agent.nodes.response_node import ResponseNode
        replay = ResponseNode(replace(node.services, judge_factory=RecordedJudge))
        state.reply = candidate
        record["candidate_gate_accepted"] = bool(candidate and await validate_candidate(replay, state))
        record["gate_issue_codes"] = sorted({item["type"] for item in state.collected_info.get("validation", {}).get("issues", [])})
        record["provider_dispatches"] = sorted(op for op in ledger.dispatched() if op.startswith(case.case_id + ":"))
        save(path, record)
        rows.append(record)
    return {"rows": rows, "evaluated_cases": len(rows), "counts": dict(Counter(r["kind"] for r in rows)),
        "whole_m5_pass": False, "quality_status": "INCOMPLETE_OUTPUT_REVIEW_AND_INDEPENDENT_EVIDENCE_REQUIRED"}


async def run_pipeline(plan: dict, output: Path, *, dry_run: bool, limits: dict, ledger_path: Path) -> dict:
    async with AsyncExitStack() as resources:
        return await _run_pipeline(plan, output, dry_run=dry_run, limits=limits, ledger_path=ledger_path, resources=resources)


async def _run_pipeline(plan: dict, output: Path, *, dry_run: bool, limits: dict, ledger_path: Path, resources) -> dict:
    import httpx
    from openai import AsyncOpenAI
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.config.config import settings
    from app.models.models import Base
    from app.repositories.knowledge_repository import KnowledgeRepository
    from app.services.knowledge_base import KnowledgeBaseService
    from app.services.llm_service import LLMService
    from app.agent.validation import ResponseValidator
    from app.agent.validation_llm import LLMRAGJudge
    from app.agent.nodes.response_node import ResponseNode, ResponseServices
    from scripts.evaluate_m5_offline_chain import IndexOnlyEmbedding, load_package, populate
    from scripts.m5_live_budget import DispatchLedger, guarded_transport
    dataset, corpus, _ = load_package(ROOT / "acceptance/m5/adjudicated-v2", ROOT / "acceptance/m5/development.json")
    ledger = DispatchLedger(ledger_path, plan["plan_id"], limits, plan["operations"])
    repo = KnowledgeRepository("sqlite:///" + (ledger_path.parent / "knowledge.db").resolve().as_posix())
    resources.callback(repo.engine.dispose)
    Base.metadata.create_all(repo.engine)
    kb = KnowledgeBaseService(repository=repo, embedding=IndexOnlyEmbedding())
    mapping_path = output / "runtime-labels.json"
    if mapping_path.exists():
        saved = json.loads(mapping_path.read_bytes())
        _, current = repo.snapshot()
        by_id = {item["id"]: item for item in current}
        mapping = {}
        for item in saved["labels"]:
            key = tuple(item["canonical"])
            value = by_id.get(item["runtime_id"])
            if value is None or value["version_id"] != item["runtime_version"]:
                raise ValueError("persisted_evidence_snapshot_missing")
            mapping[key] = value
        if set(mapping) != {(item["chunk_id"], item["version_id"]) for item in corpus}:
            raise ValueError("persisted_evidence_labels_changed")
        if any(mapping[(doc["chunk_id"], doc["version_id"])]["content"] != doc["content"] for doc in corpus):
            raise ValueError("persisted_evidence_content_changed")
    else:
        if repo.snapshot()[1] or ledger.dispatched():
            raise ValueError("persisted_evidence_mapping_missing_no_reset")
        mapping = populate(kb, corpus)
        save(mapping_path, {"labels": [{"canonical": list(key), "runtime_id": value["id"], "runtime_version": value["version_id"]} for key, value in mapping.items()]})
    engine = create_async_engine("sqlite+aiosqlite:///" + (ledger_path.parent / "gateway.db").resolve().as_posix(), poolclass=NullPool)
    resources.push_async_callback(engine.dispose)
    async with engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    underlying = httpx.MockTransport(mock_provider) if dry_run else None
    client = httpx.AsyncClient(transport=guarded_transport(ledger, model=settings.LLM_MODEL,
        endpoint=settings.LLM_API_BASE, underlying=underlying), timeout=30, follow_redirects=False, trust_env=False)
    resources.push_async_callback(client.aclose)
    service = LLMService()
    resources.push_async_callback(service.close)
    service.RETRY_DELAY = 0
    original = service._get_chat_model
    def model(config, *args, **kwargs):
        result = original(config, *args, **kwargs)
        result.async_client = AsyncOpenAI(api_key=config.api_key, base_url=config.api_base,
                                         http_client=client, max_retries=0).chat.completions
        return result
    service._get_chat_model = model
    judge = LLMRAGJudge(service)
    def forbidden():
        raise RuntimeError("business_memory_or_cache_not_allowed")
    node = ResponseNode(ResponseServices(lambda: service, ResponseValidator, lambda: judge, forbidden, lambda: kb, forbidden))
    report = await evaluate_cases(dataset, kb, mapping, service, node, judge, factory, ledger, output)
    report.update(status="T0_MOCK_REHEARSAL_NOT_ACCEPTANCE" if dry_run else "LIVE_DIAGNOSTIC_OUTPUT_REVIEW_REQUIRED",
                  ledger=ledger.snapshot(), external_model_calls=0 if dry_run else ledger.snapshot()["requests"],
                  plan_id=plan["plan_id"], performance_gate="DEFERRED_BY_USER_NOT_PASSED")
    if dry_run and report["ledger"]["requests"] != len(plan["operations"]):
        report["status"] = "FAIL_INCOMPLETE_MOCK_DISPATCH"
    return report


def worker(plan: dict, output: Path, dry_run: bool) -> dict:
    limits = plan["proposed_limits"] if dry_run else require_authorization(plan)
    if not dry_run and output.resolve() != (LIVE_ROOT / "current").resolve():
        raise ValueError("live_output_must_use_fixed_resume_directory")
    # 模型配置只保留当前主供应商，所有业务地址/遥测/备用模型/自动重试关闭。
    from dotenv import dotenv_values
    secret = "synthetic-key" if dry_run else dotenv_values(ROOT / ".env").get("LLM_API_KEY")
    if not secret:
        raise ValueError("provider_credential_missing")
    tree = ast.parse((ROOT / "app/config/config.py").read_text(encoding="utf-8-sig"))
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == "Settings":
            for field in cls.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    os.environ.pop(field.target.id, None)
    os.environ.update(plan["provider"], LLM_API_KEY=secret, LLM_GATEWAY_ENABLED="true", LLM_JUDGE_ENABLED="true",
        LLM_MAX_RETRIES="0", LLM_MAX_TOKENS="512", LLM_MAX_CONCURRENCY="1", LLM_MAX_COST_USD="1",
        LLM_DAILY_USER_TOKEN_BUDGET="10000000", LLM_DAILY_USER_COST_USD="100", REDIS_ENABLED="false",
        USE_MILVUS="false", LANGFUSE_ENABLED="false", USER_MEMORY_ENABLED="false", RAG_USE_RERANKER="false",
        AUTH_DEMO_ACCOUNTS_ENABLED="false", DATABASE_URL_OVERRIDE="sqlite+aiosqlite:///isolated.db")
    if dry_run:
        # 真正网络一律拒绝；只有SDK MockTransport和Windows socketpair能运行。
        import socket
        import threading
        local = threading.local()
        original = socket.socketpair
        def socketpair(*args, **kwargs):
            local.pair = True
            try:
                return original(*args, **kwargs)
            finally:
                local.pair = False
        socket.socketpair = socketpair
        def guard(event, args):
            if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"} and not getattr(local, "pair", False):
                raise RuntimeError("mock_rehearsal_network_forbidden")
        sys.addaudithook(guard)
    ledger_path = Path.cwd() / "mock-dispatch.sqlite" if dry_run else LIVE_ROOT / "dispatch.sqlite"
    return asyncio.run(run_pipeline(plan, output, dry_run=dry_run, limits=limits, ledger_path=ledger_path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--execute", action="store_true")
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    plan = build_plan()
    if args.worker_output:
        with exclusive_run((args.worker_output if args.dry_run else LIVE_ROOT) / "execution.lock"):
            report = worker(plan, args.worker_output, args.dry_run)
            save(args.worker_output / "report.json", report)
        return 2 if report["status"].startswith("FAIL") else 0
    if args.prepare or not (args.execute or args.dry_run):
        save(PLAN_PATH, plan)
        print(json.dumps({"status": plan["status"], "plan_id": plan["plan_id"], "proposed_limits": plan["proposed_limits"]}))
        return 0
    if args.execute:
        require_authorization(plan)  # 在启动子进程或导入LLM/读取凭据前拒绝未授权。
    from scripts.test_provenance import snapshot_sources, compare_sources
    before = snapshot_sources(ROOT)
    output = LIVE_ROOT / "current" if args.execute else ROOT / "reports/m5" / datetime.now().strftime("live-rehearsal-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=True)
    code, failure = None, None
    try:
        with tempfile.TemporaryDirectory(prefix="aicshub-m5-live-") as temporary:
            with (output / "worker.log").open("a", encoding="utf-8") as log:
                code = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                    "--execute" if args.execute else "--dry-run", "--worker-output", str(output)],
                    cwd=temporary, stdout=log, stderr=subprocess.STDOUT, timeout=7200).returncode
    except Exception as exc:
        failure = type(exc).__name__
    report = json.loads((output / "report.json").read_bytes()) if (output / "report.json").is_file() else {"status": "FAIL_NO_WORKER_REPORT", "whole_m5_pass": False}
    after = snapshot_sources(ROOT)
    report.update(compare_sources(before, after), worker_exit_code=code)
    if code != 0 or failure or not report["source_tree_stable"] or not report["source_snapshot_complete"]:
        report["status"] = "FAIL_EXECUTION_OR_SOURCE_DRIFT"
        report["error_type"] = failure or "worker_or_source_failure"
    for name, value in (("report", report), ("source-start", before), ("source-end", after)):
        save(output / f"{name}.json", value)
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if not report["status"].startswith("FAIL") else 2


if __name__ == "__main__":
    raise SystemExit(main())
