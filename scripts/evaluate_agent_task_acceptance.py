"""固定20轮任务验收：真实LLM或SDK替身、隔离SQL/词法知识库、持久HTTP预算。"""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import AsyncExitStack, chdir
from contextvars import ContextVar
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.prepare_agent_task_acceptance import (OUTPUT, PLAN, NEW_OUTPUT, require_prepared,
    require_revalidation_prepared, require_new_phase_prepared)

TURN = ContextVar("task_admission_turn", default=None)


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def operations(plan: dict) -> dict[str, int]:
    return {f"{case['case_id']}/turn-{turn}/http-{slot}": 1024
            for case in plan["cases"] for turn in range(1, len(case["turns"]) + 1)
            for slot in range(1, plan["max_provider_requests_per_turn"] + 1)}


def ledger_operations(prepared: dict, plan: dict) -> dict[str, int]:
    original = operations(plan)
    prefix = prepared.get("operation_prefix", "")
    active = {key: value for key, value in original.items()
              if not prepared.get("case_ids") or key.split("/")[0] in prepared["case_ids"]}
    previous = {"revision-v2/" + key: value for key, value in original.items()} if prepared.get("revision") == "v3" else {}
    return {**original, **previous, **{prefix + key: value for key, value in active.items()}} if prefix else original


def verify_prepared(prepared: dict) -> dict:
    value = (require_new_phase_prepared() if prepared.get("revision") == "v4" else
             require_revalidation_prepared(prepared["revision"]) if prepared.get("revision") in {"v2", "v3"} else require_prepared())
    if value != prepared:
        raise ValueError("active_preparation_changed")
    return value


def budget_directory(prepared: dict) -> Path:
    return NEW_OUTPUT if prepared.get("revision") == "v4" else OUTPUT


def authorization(prepared: dict, plan: dict) -> dict:
    from scripts.m5_live_budget import validate_authorization

    directory = (NEW_OUTPUT if prepared.get("revision") == "v4" else
                 OUTPUT / ("revalidation-" + prepared["revision"]) if prepared.get("revision") in {"v2", "v3"} else OUTPUT)
    value = json.loads((directory / "authorization.json").read_bytes())
    if value.get("prepared_id") != prepared["prepared_id"]:
        raise ValueError("authorization_must_bind_frozen_source_and_plan")
    return validate_authorization(value, {**plan, "plan_id": prepared.get("plan_id", plan["plan_id"])})


def nearest_rank(values: list[float], quantile: float) -> float | None:
    return sorted(values)[math.ceil(len(values) * quantile) - 1] if values else None


def synthetic_understanding(case: dict, turn: int) -> dict:
    conditional = case["category"] == "conditional" and turn == 1
    diagnostic = case["category"] == "diagnostic"
    knowledge_first = case["case_id"] == "mixed-02"
    topic = "technical" if diagnostic else ("refund" if conditional or knowledge_first else "query_order")
    request_type = "inquiry" if diagnostic or knowledge_first else ("apply" if conditional else "query")
    subrequests = ([{"topic": "query_order", "request_type": "query"},
                    {"topic": "refund", "request_type": "apply" if conditional else "inquiry"}]
                   if case["category"] == "mixed" or conditional else [])
    if knowledge_first:
        subrequests.reverse()
    return {"topic": topic, "request_type": request_type,
        "entities": {"order_id": None if diagnostic else "ORD960001", "product": case.get("product")},
        "conditions": [{"check": "order_delivered", "source": "current_message"}] if conditional else [],
        "subrequests": subrequests, "service_preference": "unspecified", "emotion": "neutral",
        "clarification": None, "retrieval_query": (case.get("product", "") + " 排障步骤" if diagnostic else "退货包装及材料政策"),
        "entity_source": "history" if diagnostic and turn > 1 else "current_message",
        "read_tasks": ([{"tool": "query_order", "resource_id": "ORD960001"}]
                       if not diagnostic and not knowledge_first else []),
        "parameters": {"reason": "外壳明显破损", "refund_type": "return"} if conditional else {},
        "missing_information": [], "task_kind": "troubleshoot" if diagnostic else ("after_sales" if conditional else ("multi" if subrequests else "single")),
        "continue_task": diagnostic and turn > 1}


def mock_provider(request, cases: dict, *, diagnostic_duplicate: bool = False):
    import httpx

    payload = json.loads(request.content)
    case_id, turn_text = TURN.get().split("/turn-")
    case, turn = cases[case_id], int(turn_text)
    function = payload["tools"][0]["function"]["name"]
    if function == "TaskRequestUnderstanding":
        result = synthetic_understanding(case, turn)
    elif function == "TaskDecision":
        context = json.loads(payload["messages"][-1]["content"])
        observations = {item["capability"] for item in context["observations"]}
        if case["category"] == "diagnostic":
            result = ({"action": "search_knowledge", "knowledge_focus": "troubleshooting"}
                      if diagnostic_duplicate and turn == 2 else {"action": "ask_user", "question": "diagnostic_feedback"})
        elif "query_order" not in observations:
            result = {"action": "read", "reads": [{"tool": "query_order", "order_id": "ORD960001"}]}
        elif "hybrid_search" not in observations:
            result = {"action": "search_knowledge", "knowledge_focus": "refund_policy" if case["category"] == "conditional" else "request"}
        elif case.get("sandbox_refund_preview"):
            result = {"action": "prepare_refund"}
        elif case["category"] == "conditional":
            result = {"action": "ask_user", "question": "request"}
        else:
            result = {"action": "finish"}
        result = {"step": result}
    elif function == "LLMResponse":
        text = ("请检查设备电源连接，再告诉我指示灯是否亮起。" if case["category"] == "diagnostic" else
                "订单ORD960001目前" + ("已发货" if case.get("order_status") == "shipped" else "已签收")
                + "；退货需要保留原包装和配件，并提供订单号及破损照片。")
        result = {"reply": text}
    elif function == "_JudgeVerdict":
        result = {"grounded": True, "fact_score": 1., "has_unsupported_claim": False,
                  "refuse_recommended": False, "reason": "synthetic protocol fixture only"}
    else:
        raise ValueError("unexpected_mock_schema:" + function)
    return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
        "model": payload["model"], "choices": [{"index": 0, "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "synthetic-call",
        "type": "function", "function": {"name": function, "arguments": json.dumps(result, ensure_ascii=False)}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})


def assess_case(case: dict, turns: list[dict], sql: dict) -> dict:
    checks = {"all_turns": len(turns) == len(case["turns"]),
              "http_success": all(row.get("http_status") == 200 and row.get("reply") for row in turns),
              "no_handoff_or_ticket": sql["tickets"] == sql["handoffs"] == 0,
              "no_unconfirmed_submission": all(row["applications"] == 0 for row in turns[:1]),
              "replay_no_new_calls": all(row.get("replay_no_new_calls", False) for row in turns)}
    if case["category"] == "mixed":
        calls = {name for row in turns for name in row["capabilities"]}
        checks["required_capabilities"] = set(case["required_capabilities"]) <= calls
        checks["no_application_or_preview"] = sql["applications"] == sql["previews"] == 0
        if case["order_owner"] == "other":
            checks["safe_stop"] = turns[0].get("task_status") == "stopped"
            checks["no_foreign_values"] = not any(word in turns[0]["reply"] for word in ("已签收", "0.10", "合成保密地址"))
        else:
            checks["completed"] = turns[0].get("task_status") == "completed"
            checks["both_answers"] = "包装" in turns[0]["reply"] or "材料" in turns[0]["reply"]
            checks["current_order_answer"] = ("已发货" if case["order_status"] == "shipped" else "已签收") in turns[0]["reply"]
    elif case["category"] == "diagnostic":
        checks["waits_for_feedback"] = all(row.get("task_status") == "waiting_input" for row in turns)
        checks["same_task"] = len({row.get("task_id") for row in turns}) == 1 and bool(turns[0].get("task_id"))
        checks["fresh_retrieval_each_turn"] = all("search_knowledge" in row["capabilities"] for row in turns)
        checks["no_business_write"] = sql["applications"] == sql["previews"] == 0
        from app.agent.diagnostic_feedback import current_feedback_report, diagnostic_claim_issues
        from app.agent.answer_review import answer_body
        checks["no_unobserved_diagnostic_claim"] = all(not diagnostic_claim_issues(
            answer_body(row["reply"]), current_feedback_report(row["message"])) for row in turns)
    else:
        expected_applications = 1 if case["case_id"] == "conditional-01" else 0
        checks["applications"] = sql["applications"] == expected_applications
        checks["previews"] = sql["previews"] == int(case.get("sandbox_refund_preview", False))
        if case.get("sandbox_refund_preview"):
            checks["independent_confirmation"] = turns[0].get("task_status") == "waiting_confirmation" and turns[0]["applications"] == 0
        else:
            checks["readonly_gate"] = sql["previews"] == sql["applications"] == 0
    return {"passed": all(checks.values()), "checks": checks,
            "manual_semantic_review_required": True}


async def pipeline(prepared: dict, plan: dict, output: Path, limits: dict, dry: bool) -> dict:
    import httpx
    from openai import AsyncOpenAI
    from fastapi import FastAPI
    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.config.config import settings
    from app.models.models import Base, User, Order, RefundApplication, RefundAction, Ticket, HandoffRequestRecord
    from scripts.m5_live_budget import DispatchLedger, OPERATION, guarded_transport
    from app.services.llm_service import LLMService

    ledger = DispatchLedger((output if dry else budget_directory(prepared)) / "dispatch.sqlite",
        prepared.get("ledger_prepared_id", prepared["prepared_id"]), limits, ledger_operations(prepared, plan))
    initial_requests = ledger.snapshot()["requests"]
    cases = {case["case_id"]: case for case in plan["cases"]
             if not prepared.get("case_ids") or case["case_id"] in prepared["case_ids"]}
    guarded = guarded_transport(ledger, model=settings.LLM_MODEL, endpoint=settings.LLM_API_BASE,
        underlying=httpx.MockTransport(lambda request: mock_provider(request, cases,
            diagnostic_duplicate=prepared.get("revision") == "v3")) if dry else None)
    counts, capabilities = {}, {}

    class Capture(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            key = TURN.get()
            if key is None:
                raise ValueError("model_dispatch_without_fixed_turn")
            counts[key] = counts.get(key, 0) + 1
            operation = prepared.get("operation_prefix", "") + key + "/http-" + str(counts[key])
            token = OPERATION.set(operation)
            began = time.perf_counter()
            try:
                response = await guarded.handle_async_request(request)
                value = response.json() if response.status_code == 200 else {}
                save(output / "provider" / (operation.replace("/", "_") + ".json"), {
                    "operation": operation, "latency_ms": (time.perf_counter() - began) * 1000,
                    "http_status": response.status_code, **{name: value.get(name) for name in ("choices", "usage", "model")}})
                return response
            finally:
                OPERATION.reset(token)

        async def aclose(self):
            await guarded.aclose()

    async with AsyncExitStack() as resources:
        engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
        resources.push_async_callback(engine.dispose)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        import app.utils.database as database
        database.async_session = factory
        async with factory() as db:
            db.add_all([User(id=101, username="task-synthetic-self", role="user", token_version=0, status=True),
                        User(id=202, username="task-synthetic-other", role="user", token_version=0, status=True)])
            await db.commit()

        from app.repositories.knowledge_repository import KnowledgeRepository
        from app.services.knowledge_base import KnowledgeBaseService
        from app.services.knowledge_base.index_worker import index_job
        from app.models.models import BackgroundJob

        class SyntheticEmbedding:
            _dim = 3
            backend_name = "task-admission-synthetic-v1"

            def encode_batch(self, texts):
                return [[1., 0., 0.] for _ in texts]

        class LexicalKnowledge(KnowledgeBaseService):
            async def async_search(self, **kwargs):
                capabilities.setdefault(TURN.get(), []).append("search_knowledge")
                return await asyncio.to_thread(self.search, **kwargs, retrieval_mode="bm25")

        repository = KnowledgeRepository("sqlite:///" + (output / "knowledge.db").resolve().as_posix())
        resources.callback(repository.engine.dispose)
        Base.metadata.create_all(repository.engine)
        knowledge = LexicalKnowledge(repository=repository, embedding=SyntheticEmbedding())
        knowledge.initialize()
        documents = [("退货包装及材料政策", "合成政策：退货需要保留原包装和配件，并提供订单号及外壳破损照片。是否具备资格必须查询本人订单并另行核验；模拟申请不代表审核通过或退款到账。")]
        documents += [(product + "排障步骤", product + "故障排查：首先检查电源连接并观察指示灯，询问用户结果；若电源正常但故障仍在，检查数据或网络连接，再等待用户反馈。不要重复用户已经验证无效的检查；仍无法恢复时可建议联系支持，不得声称已经修复。") for product in ("耳机", "打印机", "摄像头", "路由器")]
        for title, text in documents:
            result = knowledge.add_document(title, text)
            with repository.transaction() as db:
                job = db.get(BackgroundJob, result["job_id"])
                job.status, job.lease_token = "running", "task-admission"
                job.lease_expires_at = datetime.utcnow() + timedelta(minutes=5)
            index_job(repository, knowledge._embedding_service, None, result["job_id"], "task-admission")

        import app.services.knowledge_base as knowledge_module
        knowledge_module._knowledge_base = knowledge
        provider = httpx.AsyncClient(transport=Capture(), timeout=30, trust_env=False, follow_redirects=False)
        resources.push_async_callback(provider.aclose)
        original_model = LLMService._get_chat_model

        def model(self, config, **kwargs):
            result = original_model(self, config, **kwargs)
            sdk = AsyncOpenAI(api_key=config.api_key, base_url=config.api_base, http_client=provider, max_retries=0)
            result.root_async_client, result.async_client = sdk, sdk.chat.completions
            return result

        LLMService._get_chat_model = model
        resources.callback(setattr, LLMService, "_get_chat_model", original_model)
        import app.routers.chat as chat
        from app.agent.memory import SessionManager
        import app.services.chat_service as chat_module
        import app.services.session_creation as creation
        manager = SessionManager()
        manager._redis_available = False
        chat.session_manager = chat_module.session_manager = creation.session_manager = manager
        resources.push_async_callback(chat.chat_service.agent_graph.llm_service.close)
        from app.agent.tools import QueryOrderTool, ApplyRefundTool, ToolExecutionResult
        import app.agent.mcp_client as mcp
        import app.agent.nodes.tool_node as tool_node

        async def invoke(name, **payload):
            if name not in {"query_order", "apply_refund"}:
                raise ValueError("capability_outside_task_acceptance")
            capabilities.setdefault(TURN.get(), []).append(name)
            value = await (QueryOrderTool() if name == "query_order" else ApplyRefundTool()).execute(**payload)
            return ToolExecutionResult(**{k: v for k, v in value.items() if k in ToolExecutionResult.model_fields})

        mcp.invoke_tool = tool_node.invoke_tool = invoke
        from app.security import get_current_user
        from app.utils.database import get_db
        app = FastAPI()
        app.include_router(chat.router)

        async def db_override():
            async with factory() as db:
                yield db

        async def auth_override():
            async with factory() as db:
                return await db.get(User, 101)

        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_current_user] = auth_override
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic", timeout=60)
        resources.push_async_callback(client.aclose)
        rows = []
        for case in cases.values():
            if not dry:
                verify_prepared(prepared)
            settings.AGENT_TASK_REFUND_PREVIEW_ENABLED = case.get("sandbox_refund_preview", False)
            async with factory() as db:
                order = await db.get(Order, "ORD960001")
                if order is None:
                    order = Order(order_id="ORD960001")
                    db.add(order)
                order.user_id = 202 if case.get("order_owner") == "other" else 101
                order.status_code = case.get("order_status", "delivered")
                order.status = "已发货" if order.status_code == "shipped" else "已签收"
                order.total_amount, order.refunded_amount, order.items = "0.10", "0.00", []
                order.received_at, order.address = datetime.utcnow() - timedelta(days=1), "合成保密地址"
                await db.commit()
            response = await client.post("/api/v1/chat/sessions?user_id=101")
            response.raise_for_status()
            sid = response.json()["data"]["id"]
            turns = []
            for number, message in enumerate(case["turns"], 1):
                if not dry:
                    verify_prepared(prepared)
                if number == 2 and case.get("before_second_turn_status"):
                    async with factory() as db:
                        order = await db.get(Order, "ORD960001")
                        order.status_code, order.status = case["before_second_turn_status"], "已发货"
                        await db.commit()
                key = f"{case['case_id']}/turn-{number}"
                token = TURN.set(key)
                began = time.perf_counter()
                body = {"user_id": 101, "session_id": sid, "message": message, "client_message_id": key}
                try:
                    response = await client.post("/api/v1/chat/send", json=body)
                    elapsed = (time.perf_counter() - began) * 1000
                    value = response.json() if response.status_code == 200 else {}
                    before_replay = ledger.snapshot()["requests"]
                    replay = await client.post("/api/v1/chat/send", json=body)
                    replay_ok = (response.status_code == replay.status_code == 200 and replay.json() == value
                                 and ledger.snapshot()["requests"] == before_replay)
                    async with factory() as db:
                        applications = await db.scalar(select(func.count()).select_from(RefundApplication).where(RefundApplication.session_id == sid))
                    row = {"turn": key, "message": message, "http_status": response.status_code,
                        "latency_ms": elapsed, "reply": value.get("reply", ""),
                        "task_id": value.get("task_id"), "task_status": value.get("task_status"),
                        "capabilities": capabilities.get(key, []), "provider_requests": counts.get(key, 0),
                        "applications": applications, "replay_no_new_calls": replay_ok}
                finally:
                    TURN.reset(token)
                save(output / "turns" / (key.replace("/", "_") + ".json"), row)
                turns.append(row)
            async with factory() as db:
                sql = {}
                for name, table in (("applications", RefundApplication), ("previews", RefundAction),
                                    ("tickets", Ticket), ("handoffs", HandoffRequestRecord)):
                    sql[name] = await db.scalar(select(func.count()).select_from(table).where(table.session_id == sid))
            case_row = {"case_id": case["case_id"], "category": case["category"], "turns": turns,
                        "sql": sql, **assess_case(case, turns, sql)}
            rows.append(case_row)
            save(output / "cases" / (case["case_id"] + ".json"), case_row)
            # Keep quality failures in the denominator; stop immediately on an unsafe write.
            if not case_row["checks"]["no_unconfirmed_submission"] or not case_row["checks"]["no_handoff_or_ticket"]:
                break
    latencies = [turn["latency_ms"] for row in rows for turn in row["turns"]]
    return {"status": "MOCK_PROTOCOL_ONLY" if dry else "REAL_DIAGNOSTIC_REQUIRES_REVIEW",
        "cases": rows, "evaluated_cases": len(rows), "expected_cases": len(cases),
        "evaluated_turns": len(latencies), "expected_turns": sum(len(case["turns"]) for case in cases.values()),
        "objective_cases_passed": sum(row["passed"] for row in rows), "ledger": ledger.snapshot(),
        "external_model_calls": 0 if dry else ledger.snapshot()["requests"],
        "phase_provider_requests": 0 if dry else ledger.snapshot()["requests"] - initial_requests,
        "latency_ms": {"p50": nearest_rank(latencies, .5), "p95": nearest_rank(latencies, .95), "max": max(latencies) if latencies else None},
        "latency_scope": "sequential isolated ASGI+SQLite+BM25+LLM, excludes real embedding/reranker, socket transport and browser",
        "independent_human_labels": False, "business_enabled": False,
        "manual_semantic_review_required": True, "production_slo_accepted": False}


def worker(prepared: dict, plan: dict, output: Path, dry: bool) -> dict:
    from dotenv import dotenv_values

    limits = plan["proposed_limits"] if dry else authorization(prepared, plan)
    config = dotenv_values(ROOT / ".env")
    provider = prepared["provider"]
    key = "synthetic-key" if dry else config.get("LLM_API_KEY")
    if not key:
        raise ValueError("credential_missing")
    tree = ast.parse((ROOT / "app/config/config.py").read_text(encoding="utf-8-sig"))
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef) and cls.name == "Settings":
            for field in cls.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    os.environ.pop(field.target.id, None)
    with tempfile.TemporaryDirectory(prefix="aicshub-task-admission-") as temporary, chdir(temporary):
        os.environ.update(provider, LLM_API_KEY=key, LLM_MAX_RETRIES="0", LLM_MAX_TOKENS="1024",
            LLM_GATEWAY_ENABLED="true", LLM_UNDERSTANDING_MAX_TOKENS="500", LLM_MAX_CONCURRENCY="1",
            LLM_DAILY_USER_TOKEN_BUDGET="10000000", LLM_DAILY_USER_COST_USD="100", LLM_MAX_COST_USD="1",
            REQUEST_UNDERSTANDING_MODE="model", REQUEST_UNDERSTANDING_PERCENTAGE="100",
            AGENT_TASK_LOOP_ENABLED="true", AGENT_TASK_REFUND_PREVIEW_ENABLED="false",
            LLM_JUDGE_ENABLED="true", LLM_JUDGE_CONTRACT_VERSION="v1",
            REDIS_ENABLED="false", USE_MILVUS="false", LANGFUSE_ENABLED="false", USER_MEMORY_ENABLED="false",
            AUTH_DEMO_ACCOUNTS_ENABLED="false", RAG_USE_RERANKER="false",
            DATABASE_URL_OVERRIDE="sqlite+aiosqlite:///" + (output / "business.db").resolve().as_posix())
        if dry:
            # Let asyncio create its private Windows wakeup socket pair, reject all external I/O.
            import socket
            import threading
            local, original = threading.local(), socket.socketpair

            def pair(*args, **kwargs):
                local.active = True
                try:
                    return original(*args, **kwargs)
                finally:
                    local.active = False

            socket.socketpair = pair

            def audit(event, _args):
                if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"} and not getattr(local, "active", False):
                    raise RuntimeError("task_mock_network_forbidden")

            sys.addaudithook(audit)
        return asyncio.run(pipeline(prepared, plan, output, limits, dry))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--execute", action="store_true")
    parser.add_argument("--revision-v2", action="store_true")
    parser.add_argument("--revision-v3", action="store_true")
    parser.add_argument("--revision-v4", action="store_true")
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sum((args.revision_v2, args.revision_v3, args.revision_v4)) > 1:
        raise ValueError("select_one_revision")
    revision = "v4" if args.revision_v4 else ("v3" if args.revision_v3 else ("v2" if args.revision_v2 else None))
    prepared = require_new_phase_prepared() if revision == "v4" else (require_revalidation_prepared(revision) if revision else require_prepared())
    plan = json.loads(PLAN.read_bytes())
    active_output = NEW_OUTPUT if revision == "v4" else (OUTPUT / ("revalidation-" + revision) if revision else OUTPUT)
    if args.execute:
        authorization(prepared, plan)
    output = (args.worker_output or (active_output / "current" if args.execute else
              active_output / "rehearsals" / datetime.now().strftime("%Y%m%d-%H%M%S-%f"))).resolve()
    if args.execute and output != (active_output / "current").resolve():
        raise ValueError("fixed_live_output_required")
    if args.dry_run and not output.is_relative_to((active_output / "rehearsals").resolve()):
        raise ValueError("mock_output_must_not_overlap_live")
    output.mkdir(parents=True, exist_ok=True)
    if args.worker_output:
        result = worker(prepared, plan, output, args.dry_run)
        save(output / "report.json", result)
        return 0
    if (output / "report.json").exists() or (args.execute and not revision and (OUTPUT / "dispatch.sqlite").exists()):
        raise ValueError("prior_live_attempt_exists_no_automatic_retry_or_overwrite")
    if args.execute and revision:
        import sqlite3
        if (budget_directory(prepared) / "dispatch.sqlite").exists():
            with sqlite3.connect(budget_directory(prepared) / "dispatch.sqlite") as db:
                if db.execute("SELECT count(*) FROM dispatches WHERE operation LIKE ?",
                              (prepared.get("operation_prefix", "") + "%",)).fetchone()[0]:
                    raise ValueError("prior_revalidation_dispatch_exists_no_automatic_retry")
    from scripts.test_provenance import snapshot_sources, compare_sources
    before = snapshot_sources(ROOT)
    from scripts.evaluate_m5_live import exclusive_run
    with exclusive_run((budget_directory(prepared) if args.execute else output) / "execution.lock"), (output / "worker.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()),
            "--execute" if args.execute else "--dry-run", *(["--revision-" + revision] if revision else []),
            "--worker-output", str(output)], cwd=ROOT, stdout=log, stderr=log)
    after = snapshot_sources(ROOT)
    save(output / "source-start.json", before)
    save(output / "source-end.json", after)
    result_path = output / "report.json"
    if result_path.exists():
        result = json.loads(result_path.read_bytes())
    else:
        from scripts.m5_live_budget import DispatchLedger
        ledger_path = (output if args.dry_run else budget_directory(prepared)) / "dispatch.sqlite"
        limits = plan["proposed_limits"] if args.dry_run else authorization(prepared, plan)
        ledger = DispatchLedger(ledger_path, prepared.get("ledger_prepared_id", prepared["prepared_id"]),
                                limits, ledger_operations(prepared, plan)) if ledger_path.exists() else None
        rows = [json.loads(path.read_bytes()) for path in sorted((output / "cases").glob("*.json"))]
        result = {"status": "WORKER_FAILED", "cases": rows, "expected_cases": prepared["cases"],
                  "evaluated_cases": len(rows), "expected_turns": prepared["turns"],
                  "objective_cases_passed": sum(row["passed"] for row in rows),
                  "ledger": ledger.snapshot() if ledger else None,
                  "external_model_calls": 0 if args.dry_run else (ledger.snapshot()["requests"] if ledger else 0),
                  "business_enabled": False}
    result.update(compare_sources(before, after), worker_exit_code=completed.returncode,
                  prepared_id=prepared["prepared_id"])
    save(result_path, result)
    print(json.dumps({"status": result["status"], "report": str(result_path),
                      "objective_cases_passed": result.get("objective_cases_passed"),
                      "external_model_calls": result.get("external_model_calls")}, ensure_ascii=False))
    return 0 if (completed.returncode == 0 and result["source_tree_stable"] and result["source_snapshot_complete"]
                 and result.get("objective_cases_passed") == prepared["cases"]
                 and result.get("evaluated_turns") == prepared["turns"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
