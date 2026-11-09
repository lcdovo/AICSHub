"""新模型理解：受控6例真实冒烟。批量质量和切换由独立复核门禁阻止，预算不重置。"""
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
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCOPE = ROOT / "docs/MODEL-FIRST-ADMISSION-PLAN-v1.json"
DATA = ROOT / "acceptance/model-first/smoke-v1.json"
RUN = ROOT / "reports/model-first-admission"


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def operations() -> dict[str, int]:
    return {f"{phase}-{i:03d}": 500 for phase, count in
            (("smoke", 6), ("quality", 300), ("critical", 60), ("startup", 6))
            for i in range(1, count + 1)}


def authorized_limits() -> dict:
    from scripts.m5_live_budget import validate_authorization
    scope = json.loads(SCOPE.read_bytes())
    return validate_authorization(json.loads((RUN / "authorization.json").read_bytes()),
                                  {**scope, "plan_id": sha(SCOPE)})


def prepared_plan() -> dict:
    from scripts.evaluate_m5_live import public_config
    from scripts.test_provenance import snapshot_sources
    provider = public_config()
    if (provider["LLM_MODEL"] != "deepseek-flash"
            or provider["LLM_API_BASE"].rstrip("/") != "https://token.uino.com/v1"
            or provider["LLM_STRUCTURED_OUTPUT_METHOD"] != "function_calling"
            or provider["LLM_THINKING_MODE"] != "disabled"):
        raise ValueError("provider_outside_approved_scope")
    data = json.loads(DATA.read_bytes())
    if [case["case_id"] for case in data["cases"]] != [f"smoke-{i:03d}" for i in range(1, 7)]:
        raise ValueError("smoke_case_identity_changed")
    protected = [
        "reports/m4-closeout/live/authorization-ledger.json",
        "reports/m5-live/authorization.json", "reports/m5-live/dispatch.sqlite",
        "reports/m5-live/current/report.json",
        "reports/m5-judge-v2-live/authorization.json",
        "reports/m5-judge-v2-live/dispatch.sqlite", "reports/m5-judge-v2-live/current/report.json",
    ]
    source = snapshot_sources(ROOT)
    if source["unsupported_paths"]:
        raise ValueError("incomplete_source_snapshot")
    result = {
        "schema_version": 1, "phase": "smoke_only", "budget_scope_id": sha(SCOPE),
        "source_fingerprint": source["fingerprint"], "dataset_sha256": sha(DATA),
        "provider": provider, "output_cap": 500,
        "protected_prior_hashes": {p: sha(ROOT / p) for p in protected},
        "independent_human_labels": False, "whole_admission_pass": False,
        "bulk_live_allowed": False, "automatic_retry": False, "automatic_repair": False,
    }
    result["prepared_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def require_prepared() -> dict:
    expected = json.loads((RUN / "prepared-smoke.json").read_bytes())
    if prepared_plan() != expected:
        raise ValueError("prepared_source_dataset_or_configuration_changed")
    return expected


def assess_case(case: dict, parsed, state) -> dict:
    from app.agent.request_understanding import validate_semantics, route_understanding, execution_request_type, execution_missing_information
    from app.agent.routing_policy import DEFAULT_ROUTING_POLICY
    from app.agent.nodes.tool_node import select_tools
    if parsed is None:
        return {"passed": False, "schema_valid": False, "native_schema_valid": False, "protected": True,
                "reason": "missing_or_invalid_provider_output"}
    try:
        validate_semantics(parsed, state)
    except (ValueError, TypeError):
        return {"passed": False, "schema_valid": False, "native_schema_valid": True, "protected": True,
                "reason": "semantic_or_source_contract_rejected",
                "parsed": parsed.model_dump()}
    expect = case["expected"]
    goal_topic, goal_type = parsed.topic, parsed.request_type
    if case.get("score_final_goal") and parsed.conditions and parsed.request_type == "query":
        goal = next((item for item in parsed.subrequests if item.request_type == "apply"), None)
        if goal is not None:
            goal_topic, goal_type = goal.topic, goal.request_type
    checks = {
        "topic": goal_topic in expect["topics"],
        "request_type": goal_type in expect["types"],
        "order": parsed.entities.order_id == expect["order_id"],
        "product": "product" not in expect or parsed.entities.product == expect["product"],
        "reason": "reason" not in expect or parsed.parameters.reason == expect["reason"],
        "condition": "condition" not in expect or expect["condition"] in [c.check for c in parsed.conditions],
        "read_plan": set(expect["required_reads"]).issubset({task.tool for task in parsed.read_tasks}),
    }
    next_node = route_understanding(state, parsed)
    if next_node == "route_decision":
        next_node = DEFAULT_ROUTING_POLICY.decide_structured(
            topic=state.detected_intent, request_type=execution_request_type(parsed),
            parameters=state.current_business_parameters().model_dump(exclude_none=True),
            missing_information=execution_missing_information(parsed)).next_node
    planned_tools = [item[0] for item in select_tools(state.detected_intent, state.business_context())] if next_node == "tool_execution" and not state.read_plan else [item.tool for item in state.read_plan]
    planned_writes = set(planned_tools) & {"apply_refund", "create_ticket", "escalate_to_human"}
    if "execution" in expect:
        checks["execution"] = execution_request_type(parsed) == expect["execution"]
    if expect.get("no_write_plan"):
        checks["no_write_plan"] = not planned_writes and next_node != "human_handoff"
    if "next_nodes" in expect:
        checks["next_node"] = next_node in expect["next_nodes"]
    return {"passed": all(checks.values()), "schema_valid": True, "native_schema_valid": True,
            "protected": not planned_writes and next_node != "human_handoff", "checks": checks,
            "predicted_topic": goal_topic, "predicted_request_type": goal_type,
            "next_node": next_node, "parsed": parsed.model_dump(),
            "planned_tools": planned_tools, "business_tools_executed": 0}


async def pipeline(prepared: dict, output: Path, limits: dict, dry: bool) -> dict:
    import httpx
    from openai import AsyncOpenAI
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.config.config import settings
    from app.models.models import Base
    from app.agent.state import AgentState
    from app.agent.request_understanding import PROMPT, RequestUnderstanding, input_payload
    from app.services.llm_service import LLMService
    from app.services.model_gateway import model_context
    from scripts.m5_live_budget import DispatchLedger, OPERATION, guarded_transport
    from scripts.test_provenance import snapshot_sources

    phase = prepared.get("phase", "smoke_only")
    if phase not in {"smoke_only", "critical_revalidation_v2", "reviewed_quality_v2"}:
        raise ValueError("unsupported_evaluation_phase")
    critical = phase == "critical_revalidation_v2"
    quality = phase == "reviewed_quality_v2"
    live_output = RUN / "quality-v2/current" if quality else (RUN / "critical-v2/current" if critical else RUN / "current")
    data_path = ROOT / "acceptance/model-first/quality-reviewed-v2.json" if quality else (ROOT / "acceptance/model-first/critical-v2.json" if critical else DATA)
    expected_count = 300 if quality else (60 if critical else 6)
    if not dry and output.resolve() != live_output.resolve():
        raise ValueError("real_run_requires_fixed_output")
    cases = json.loads(data_path.read_bytes())["cases"]
    prefix = "quality" if quality else ("critical" if critical else "smoke")
    if [case["case_id"] for case in cases] != [f"{prefix}-{i:03d}" for i in range(1, expected_count + 1)]:
        raise ValueError("fixed_phase_case_identity_changed")
    by_id = {case["case_id"]: case for case in cases}
    ledger_root = output if dry else RUN
    ledger = DispatchLedger(ledger_root / "dispatch.sqlite", prepared["budget_scope_id"], limits, operations())
    initial_requests = ledger.snapshot()["requests"]
    capture = {}

    def respond(request):
        payload = by_id[OPERATION.get()]["mock"]
        return httpx.Response(200, json={
            "id": "synthetic", "object": "chat.completion", "created": 1, "model": settings.LLM_MODEL,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "synthetic-call", "type": "function", "function": {
                        "name": "RequestUnderstanding", "arguments": json.dumps(payload, ensure_ascii=False)}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        })

    guarded = guarded_transport(ledger, model=settings.LLM_MODEL, endpoint=settings.LLM_API_BASE,
                                underlying=httpx.MockTransport(respond) if dry else None)

    class Capture(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            response = await guarded.handle_async_request(request)
            # 只保存合成输出、finish_reason与usage，不记录HTTP头/凭据或错误正文。
            value = response.json() if response.status_code == 200 else {}
            capture[OPERATION.get()] = {k: value.get(k) for k in ("choices", "usage", "model")}
            return response

        async def aclose(self):
            await guarded.aclose()

    rows = []
    async with AsyncExitStack() as resources:
        engine = create_async_engine("sqlite+aiosqlite:///" + (ledger_root / "gateway.db").resolve().as_posix(),
                                     poolclass=NullPool)
        resources.push_async_callback(engine.dispose)
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        client = httpx.AsyncClient(transport=Capture(), timeout=30, trust_env=False, follow_redirects=False)
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
        for case in cases:
            if not dry and snapshot_sources(ROOT)["fingerprint"] != prepared["source_fingerprint"]:
                raise ValueError("source_drift_before_dispatch")
            path = output / "cases" / (case["case_id"] + ".json")
            if path.exists():
                row = json.loads(path.read_bytes())
                if row.get("prepared_id") != prepared["prepared_id"]:
                    raise ValueError("recorded_case_preparation_mismatch_no_overwrite")
            elif case["case_id"] in ledger.dispatched():
                row = {"case_id": case["case_id"], "passed": False, "schema_valid": False,
                       "reason": "prior_dispatch_unknown_no_retry", "prepared_id": prepared["prepared_id"]}
            else:
                state = AgentState(session_id="smoke-" + case["case_id"], user_id=990007,
                                   user_message=case["message"], understanding_mode="model",
                                   messages=case.get("history", []),
                                   detected_intent=case.get("previous_intent", "general"),
                                   collected_info={"order_id": case["known_order"]} if case.get("known_order") else {})
                started = time.perf_counter()
                token = OPERATION.set(case["case_id"])
                try:
                    async with model_context(990007, case["case_id"], factory=factory,
                                             route_version="understanding.model-first.v2"):
                        parsed = await service.run_structured(PROMPT, input_payload(state), RequestUnderstanding,
                                                              tags=["request_understanding"], prefer_local=False,
                                                              max_tokens=500)
                finally:
                    OPERATION.reset(token)
                row = {"case_id": case["case_id"], "prepared_id": prepared["prepared_id"],
                       "label_source": "assistant_authored_protocol_fixture",
                       "latency_ms": (time.perf_counter() - started) * 1000,
                       **assess_case(case, parsed, state),
                       "provider_output": capture.get(case["case_id"])}
            save(path, row)
            rows.append(row)
            if critical and ((len(rows) <= 6 and not row["passed"]) or not row.get("protected", False)):
                break  # 前6条要求正确推进，其余边界区分理解错误与程序是否阻止写入。
    passed = len(rows) == expected_count and (
        all(row["passed"] for row in rows[:6]) and all(row.get("protected", False) for row in rows)
        if critical else all(row["passed"] for row in rows))
    return {
        "status": ("MOCK_PROTOCOL_ONLY" if dry else "REAL_QUALITY_COLLECTED") if quality else (("MOCK_PROTOCOL_ONLY" if dry else ("REAL_CRITICAL_PASS_REVIEW_BLOCKED" if critical else "REAL_SMOKE_PASS_REVIEW_BLOCKED")) if passed else ("FAIL_CRITICAL" if critical else "FAIL_SMOKE")),
        "smoke_pass": all(row["passed"] for row in rows[:6]) and len(rows) >= 6,
        "critical_pass": passed if critical else None,
        "functional_passed": sum(row["passed"] for row in rows),
        "evaluated_cases": len(rows), "expected_cases": expected_count,
        "phase": phase, "rows": rows, "ledger": ledger.snapshot(),
        "external_model_calls": 0 if dry else ledger.snapshot()["requests"],
        "new_provider_requests": 0 if dry else ledger.snapshot()["requests"] - initial_requests,
        "phase_provider_requests": 0 if dry else sum(op.startswith(prefix + "-") for op in ledger.dispatched()),
        "whole_admission_pass": False, "independent_human_labels": False,
        "bulk_live_allowed": False, "default_changed": False,
        "remaining_blockers": ["fresh_frozen_independently_reviewed_quality_dataset",
                               "critical_boundary_full_graph_replay", "deployment_validation"],
    }


def worker(prepared: dict, output: Path, dry: bool) -> dict:
    from dotenv import dotenv_values
    critical = prepared.get("phase") == "critical_revalidation_v2"
    quality = prepared.get("phase") == "reviewed_quality_v2"
    expected_live = RUN / "quality-v2/current" if quality else (RUN / "critical-v2/current" if critical else RUN / "current")
    if dry and not output.resolve().is_relative_to((RUN / "rehearsals").resolve()):
        raise ValueError("mock_output_must_not_overlap_live_report")
    if not dry and output.resolve() != expected_live.resolve():
        raise ValueError("real_run_requires_fixed_output")
    limits = json.loads(SCOPE.read_bytes())["proposed_limits"] if dry else authorized_limits()
    if not dry:
        if quality:
            from scripts.evaluate_model_first_quality import require_quality_prepared, quality_authorized
            quality_authorized()
            if require_quality_prepared() != prepared:
                raise ValueError("quality_preparation_mismatch")
        elif critical:
            from scripts.revalidate_model_first import require_critical_prepared, phase_authorized
            phase_authorized()
            if require_critical_prepared() != prepared:
                raise ValueError("critical_preparation_mismatch")
        else:
            require_prepared()
    key = "synthetic-key" if dry else dotenv_values(ROOT / ".env").get("LLM_API_KEY")
    if not key:
        raise ValueError("credential_missing")
    tree = ast.parse((ROOT / "app/config/config.py").read_text(encoding="utf-8-sig"))
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == "Settings":
            for field in cls.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    os.environ.pop(field.target.id, None)
    os.environ.update(prepared["provider"], LLM_API_KEY=key, LLM_MAX_RETRIES="0",
                      LLM_BACKUP_BASE="", LLM_BACKUP_MODEL="", LLM_BACKUP_KEY="",
                      LLM_LOCAL_BASE="", LLM_LOCAL_MODEL="", LLM_LOCAL_KEY="",
                      LLM_GATEWAY_ENABLED="true", LLM_UNDERSTANDING_MAX_TOKENS="500", LLM_MAX_TOKENS="500",
                      LLM_MAX_CONCURRENCY="1", LLM_DAILY_USER_TOKEN_BUDGET="10000000",
                      LLM_DAILY_USER_COST_USD="100", LLM_MAX_COST_USD="1",
                      REDIS_ENABLED="false", USE_MILVUS="false", LANGFUSE_ENABLED="false", USER_MEMORY_ENABLED="false")
    if dry:
        # 复用已有演练的标准库socket审计，拒绝一切实际网络出口。
        from scripts.evaluate_m5_judge_v2_live import socket, threading
        local = threading.local()
        pair = socket.socketpair
        def socketpair(*args, **kwargs):
            local.pair = True
            try:
                return pair(*args, **kwargs)
            finally:
                local.pair = False
        socket.socketpair = socketpair
        def audit(event, args):
            if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"} and not getattr(local, "pair", False):
                raise RuntimeError("mock_network_forbidden")
        sys.addaudithook(audit)
    return asyncio.run(pipeline(prepared, output, limits, dry))


def main() -> int:
    from scripts.evaluate_m5_live import exclusive_run
    from scripts.test_provenance import snapshot_sources, compare_sources
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--execute-smoke", action="store_true")
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.execute_smoke:
        authorized_limits()  # 在凭据读取、客户端创建或真实账本初始化前检查新授权。
    prepared = prepared_plan() if args.prepare else require_prepared()
    if args.prepare:
        if (RUN / "dispatch.sqlite").exists():
            raise ValueError("live_ledger_exists_no_refreeze")
        save(RUN / "prepared-smoke.json", prepared)
        print(json.dumps({"status": "PREPARED_SMOKE_ONLY", "prepared_id": prepared["prepared_id"]}))
        return 0
    if args.worker_output:
        with exclusive_run((args.worker_output if args.dry_run else RUN) / "execution.lock"):
            result = worker(prepared, args.worker_output, args.dry_run)
            save(args.worker_output / "report.json", result)
        return 0
    output = RUN / "current" if args.execute_smoke else RUN / "rehearsals" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=True)
    before = snapshot_sources(ROOT)
    code, failure = None, None
    try:
        with tempfile.TemporaryDirectory(prefix="aicshub-understanding-smoke-") as temporary:
            with (output / "worker.log").open("a", encoding="utf-8") as log:
                code = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                       "--execute-smoke" if args.execute_smoke else "--dry-run",
                                       "--worker-output", str(output)],
                                      cwd=temporary, stdout=log, stderr=subprocess.STDOUT, timeout=600).returncode
    except Exception as exc:
        failure = type(exc).__name__
    result = json.loads((output / "report.json").read_bytes()) if (output / "report.json").exists() else {"status": "FAIL_NO_REPORT"}
    after = snapshot_sources(ROOT)
    stable = all(sha(ROOT / path) == value for path, value in prepared["protected_prior_hashes"].items())
    result.update(compare_sources(before, after), old_ledgers_stable=stable, worker_exit_code=code,
                  whole_admission_pass=False, default_changed=False, prepared_id=prepared["prepared_id"])
    if code != 0 or failure or not result["source_tree_stable"] or not result["source_snapshot_complete"] or not stable:
        result.update(status="FAIL_SOURCE_OR_WORKER", error_type=failure or "worker_or_source")
    for name, value in (("report", result), ("source-start", before), ("source-end", after)):
        save(output / (name + ".json"), value)
    print(json.dumps({"status": result["status"], "report": str(output / "report.json")}))
    return 2 if result["status"].startswith("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())

