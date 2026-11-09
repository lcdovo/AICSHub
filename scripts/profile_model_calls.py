"""Bounded before/after graph measurement with real models and synthetic tools.

Uses a new immutable dispatch ledger; historical acceptance accounts are never
reused. Stops on the first failed independent publication review or oracle.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_multi_intent import isolated_configuration, fixture_state, save
from scripts.m5_live_budget import DispatchLedger, guarded_transport, OPERATION

CASES = ("MI-01", "MI-04", "MI-06", "MI-16")


async def evaluate(output, previous=None):
    import httpx
    from openai import AsyncOpenAI
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models.models import Base, User
    from app.config.config import settings
    from app.services.llm_service import LLMService
    from app.services.model_gateway import model_context
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.agent.tools import ToolExecutionResult
    from scripts.multi_intent_oracle import score, load_oracle
    from scripts.test_provenance import snapshot_sources
    settings.LLM_JUDGE_CONTRACT_VERSION = "v2"
    settings.LLM_JUDGE_V2_OUTPUT_PROTOCOL = "json_object"
    settings.LLM_MAX_TOKENS = 2048
    settings.LLM_MAX_RETRIES = 0
    settings.LLM_REQUEST_TIMEOUT_SECONDS = 60
    cases = {row["case_id"]: row for row in load_oracle()["cases"]}
    protected = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "reports").rglob("dispatch.sqlite") if output not in p.parents}
    operations = {f"{mode}/{case}/http-{i}": 2048 for mode in ("baseline", "optimized") for case in CASES for i in range(1, 7)}
    remaining = 48
    if previous:
        prior = json.loads((previous / "closeout.json").read_bytes())
        remaining -= prior["budget"]["requests"]
    plan = {"authorization": "依据监控数据优化调用次数、GPU 并发与 P95，授权所有流程的模型费用",
        "date": "2026-11-08", "scope": "8 real-model graphs, synthetic tools and evidence; stop on failed graph",
        "max_requests": remaining, "max_input_tokens": remaining*24576, "max_output_tokens": remaining*2048,
        "previous_stage":str(previous) if previous else None,
        "fixture_revision":"item-bound retrieval; missing source remains empty",
        "cases": list(CASES), "source": snapshot_sources(ROOT), "protected_ledgers": protected}
    source = plan["source"]
    if (output / "plan.json").exists():
        save(output / "continuation.json", {"reason":"fix shared HTTP client lifetime; resume only undispatched graphs", "source":source})
        plan = json.loads((output / "plan.json").read_bytes())
    else:
        save(output / "plan.json", plan)
    ledger = DispatchLedger(output / "dispatch.sqlite", "performance-20261108-" + output.name,
        {k:plan[k] for k in ("max_requests", "max_input_tokens", "max_output_tokens")}, operations)
    active = {}
    timings = json.loads((output / "timings.json").read_bytes()) if (output / "timings.json").exists() else []
    results = json.loads((output / "results.json").read_bytes()) if (output / "results.json").exists() else []
    completed = {(r["mode"],r["case_id"]) for r in results if r["passed"]}
    if any(not r["passed"] and r["calls"] for r in results):
        raise ValueError("failed_dispatched_graph_must_not_be_replayed")
    services = []
    inner = guarded_transport(ledger, model=settings.LLM_MODEL, endpoint=settings.LLM_API_BASE)

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            active["calls"] += 1
            if active["calls"] > 6:
                raise ValueError("graph_six_call_cap")
            operation = f'{active["mode"]}/{active["case"]}/http-{active["calls"]}'
            token = OPERATION.set(operation)
            started = time.perf_counter()
            try:
                response = await inner.handle_async_request(request)
                # Persist only synthetic input/output; never request headers.
                save(output / (operation.replace("/", "_") + ".json"), {"response": response.json()})
                timings.append({"operation": operation, "duration_ms": (time.perf_counter()-started)*1000,
                    "status": response.status_code, "usage": response.json().get("usage")})
                save(output / "timings.json", timings)
                return response
            finally:
                OPERATION.reset(token)

        async def aclose(self):
            await inner.aclose()

    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(User(id=101, username="synthetic-performance", role="user", status=True, token_version=0))
        await db.commit()
    try:
        async with httpx.AsyncClient(transport=Transport(), timeout=30, trust_env=False) as provider:
            original = LLMService._get_chat_model
            def model(self, config, **kwargs):
                result = original(self, config, **kwargs)
                sdk = AsyncOpenAI(api_key=config.api_key, base_url=config.api_base, http_client=provider, max_retries=0)
                result.root_async_client, result.async_client = sdk, sdk.chat.completions
                return result
            with patch.object(LLMService, "_get_chat_model", model):
                stop = False
                for mode in ("baseline", "optimized"):
                    if stop: break
                    settings.AGENT_TASK_DETERMINISTIC_ROUTING_ENABLED = mode == "optimized"
                    for case_id in CASES:
                        if (mode, case_id) in completed: continue
                        case = cases[case_id]
                        evidence = fixture_state(case).current_evidence()
                        class Knowledge:
                            def evidence_is_current(self, items):
                                return all(row.get("version_id") == "synthetic-v1" for row in items)
                            async def async_search(self, query, top_k):
                                selected = [entry for index, content in enumerate(case["knowledge"])
                                    if content and case["items"][index]["text"] in query
                                    for entry in evidence if entry["id"].endswith(f"-i{index+1}")]
                                return {"success":True, "results":selected, "meta":{"score_type":"rrf", "knowledge_epoch":1},
                                    "evidence_bundle":{"schema_version":1, "items":selected, "knowledge_epoch":1}}
                        async def read(tasks, principal):
                            if principal.user_id != 101 or any(t.tool != "query_order" or t.resource_id != case["order_id"] for t in tasks):
                                raise ValueError("synthetic_read_scope")
                            return [({"user_id":101,"order_id":t.resource_id}, ToolExecutionResult(success=True, data={
                                "order_id":t.resource_id,"status_code":"delivered","status":"已签收","total_amount":"99.10"})) for t in tasks]
                        active.update(mode=mode, case=case_id, calls=0)
                        started = time.perf_counter()
                        with patch("app.agent.graph.get_knowledge_base", lambda: Knowledge()), patch("app.agent.read_plan.run_authorized_reads", read):
                            graph = AgentGraph()
                            services.append(graph.llm_service)
                            try:
                                state = AgentState(session_id=f"perf-{mode}-{case_id}", user_id=101, actor_id=101,
                                    source_message_id=17, user_message=case["message"], understanding_mode="model", task_loop_enabled=True)
                                async with model_context(101, f"perf-{mode}-{case_id}", factory=factory):
                                    result = await graph.run(state)
                            finally:
                                pass  # Shared provider is owned by the outer evaluation.
                        scoring = score(case, protocol_valid=bool(result.review_result and result.review_result.status != "unknown"),
                            response_supported=bool(result.review_result and result.review_result.passed),
                            item_checks=[r.model_dump() for r in result.task_runtime.item_reviews] if result.task_runtime else None,
                            task_status=result.task.status if result.task else None, require_task=True)
                        row = {"mode":mode, "case_id":case_id, "calls":active["calls"], "duration_ms":(time.perf_counter()-started)*1000,
                            "passed":scoring["passed"], "score":scoring, "reply":result.reply,
                            "review":result.review_result.model_dump() if result.review_result else None,
                            "task_status":result.task.status if result.task else None}
                        results.append(row); save(output / "results.json", results)
                        print(json.dumps({k:v for k,v in row.items() if k in {"mode","case_id","calls","duration_ms","passed","task_status"}}), flush=True)
                        if not row["passed"]:
                            stop = True; break
    finally:
        for service in services:
            await service.close()
        await engine.dispose()
        save(output / "closeout.json", {"scope":plan["scope"], "results":results, "budget":ledger.snapshot(),
            "protected_ledgers_unchanged":all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in protected.items()),
            "source_unchanged":snapshot_sources(ROOT)["fingerprint"]==source["fingerprint"],
            "passed":len({(r["mode"],r["case_id"]) for r in results if r["passed"]})==8,
            "production_deployed":False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-undispatched", action="store_true")
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args(); output = args.output.resolve()
    previous = args.previous.resolve() if args.previous else None
    if output.exists() and not args.resume_undispatched: raise ValueError("refusing_to_overwrite_evidence")
    with isolated_configuration(live=True):
        asyncio.run(evaluate(output, previous))


if __name__ == "__main__": main()
