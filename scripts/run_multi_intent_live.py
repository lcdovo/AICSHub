"""授权后固定语义评测；--rehearse只走真实SDK/合成HTTP，不能证明模型质量。"""
from __future__ import annotations
from contextlib import closing

import argparse
import asyncio
from contextlib import AsyncExitStack
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import socket
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts.evaluate_multi_intent import (DATA, isolated_configuration, fixture_state, candidate,
                                           score_plan, save, provider_stamp)

ACTIVE = ContextVar("multi_intent_eval_operation", default=None)


class TwoAccountLedger:
    """Consume the original account first; never change its authorization or refund unknown calls."""
    def __init__(self, primary, supplement):
        self.primary,self.supplement=primary,supplement

    @property
    def limits(self): return self.snapshot()["limits"]

    def dispatched(self): return self.primary.dispatched()|self.supplement.dispatched()

    def reserve(self,operation,body):
        if operation in self.primary.dispatched() or operation in self.supplement.dispatched():
            raise ValueError("operation_already_dispatched_no_retry")
        account=self.primary if self.primary.snapshot()["requests"]<self.primary.limits["max_requests"] else self.supplement
        account.reserve(operation,body)

    def settle(self,operation,response):
        if operation in self.primary.dispatched(): self.primary.settle(operation,response)
        elif operation in self.supplement.dispatched(): self.supplement.settle(operation,response)
        else: raise ValueError("settlement_without_reserved_operation")

    def snapshot(self):
        primary,supplement=self.primary.snapshot(),self.supplement.snapshot()
        summed={key:primary[key]+supplement[key] for key in (
            "requests","input_reserved_upper_bound","output_reserved","known_actual_input_tokens",
            "known_actual_output_tokens","usage_unknown_requests")}
        return {**summed,"limits":{key:primary["limits"][key]+supplement["limits"][key]
                for key in primary["limits"]},"input_basis":primary["input_basis"],
                "accounts":{"original_unchanged_authorization":primary,"separately_authorized_supplement":supplement}}


def operation_limits(dataset, prefix="", *, end_to_end_limit=6, historical_prefixes=(), preserved_operations=()):
    if type(end_to_end_limit) is not int or not 1 <= end_to_end_limit <= 6:
        raise ValueError("end_to_end_operation_limit_invalid")
    operations = {f"{case['case_id']}/{stage}":1024 for case in dataset["cases"]
                  for stage in ("understanding","good","bad")}
    operations.update({f"E2E-{case_id}/http-{index}":1024
        for case_id in ["MI-01","MI-04","MI-06","MI-16"] for index in range(1,7)})
    if not prefix: return operations
    current={key:value for key,value in operations.items()
             if not key.startswith("E2E-") or int(key.rsplit("-",1)[1])<=end_to_end_limit}
    return {**operations, **{old+key:value for old in historical_prefixes for key,value in operations.items()},
            **{key:1024 for key in preserved_operations if not key.startswith(prefix)},
            **{prefix+key:value for key,value in current.items()}}


def validated_plan(path: Path, authorization: Path | None, *, rehearse=False):
    from scripts.test_provenance import snapshot_sources
    value = json.loads(path.read_bytes())
    unsigned={key:item for key,item in value.items() if key!="prepared_id"}
    if hashlib.sha256(json.dumps(unsigned,sort_keys=True).encode()).hexdigest()!=value["prepared_id"]:
        raise ValueError("prepared_plan_hash_invalid")
    if value["source_fingerprint"] != snapshot_sources(ROOT)["fingerprint"] or value["dataset_sha256"] != hashlib.sha256(DATA.read_bytes()).hexdigest():
        raise ValueError("prepared_source_or_dataset_changed")
    if value.get("carried_evidence"):
        artifact=ROOT/value["carried_evidence"]["path"]
        if (not artifact.resolve().is_relative_to(ROOT.resolve()) or hashlib.sha256(artifact.read_bytes()).hexdigest()!=value["carried_evidence"]["sha256"]):
            raise ValueError("carried_evidence_changed")
    if not rehearse:
        if value["provider"]!=provider_stamp():
            raise ValueError("prepared_provider_configuration_changed")
        if authorization is None:
            raise ValueError("explicit_new_phase_authorization_required_old_nine_requests_not_reused")
        approved = json.loads(authorization.read_bytes())
        if (approved.get("confirmed") is not True or approved.get("prepared_id") != value["prepared_id"]
                or approved.get("max_requests") != value["max_http_requests"]
                or approved.get("max_input_tokens") != value["max_input_bytes_reserved"]
                or approved.get("max_output_tokens") != value["max_output_tokens_reserved"]):
            raise ValueError("authorization_does_not_match_fixed_plan")
        supplement=value.get("supplemental_limits")
        if supplement is not None and (supplement!={"max_requests":24,"max_input_tokens":24*24576,"max_output_tokens":24*1024}
                or approved.get("supplemental_confirmed") is not True or approved.get("supplemental_limits")!=supplement):
            raise ValueError("separate_supplement_authorization_required")
        extra=value.get("extra_supplemental_limits")
        if extra is not None and (extra!={"max_requests":12,"max_input_tokens":12*24576,"max_output_tokens":12*1024}
                or supplement is None or approved.get("extra_supplemental_confirmed") is not True
                or approved.get("extra_supplemental_limits")!=extra):
            raise ValueError("separate_extra_supplement_authorization_required")
        continuation=value.get("continuation_limits")
        if continuation is not None and (continuation!={"max_requests":6,"max_input_tokens":6*24576,"max_output_tokens":6*1024}
                or extra is None or approved.get("continuation_confirmed") is not True
                or approved.get("continuation_limits")!=continuation):
            raise ValueError("separate_continuation_authorization_required")
        if value.get("carried_evidence") and approved.get("segmented_resume_confirmed") is not True:
            raise ValueError("segmented_resume_authorization_required")
        if value.get("phase_prepared_id"):
            if (approved.get("revalidation_confirmed") is not True
                    or approved.get("phase_prepared_id")!=value["phase_prepared_id"]):
                raise ValueError("explicit_revalidation_approval_required")
            import sqlite3
            ledger=ROOT/"reports/multi-intent/live-budget"/value["phase_prepared_id"]/"dispatch.sqlite"
            if not ledger.exists(): raise ValueError("original_ledger_missing")
            with closing(sqlite3.connect(ledger)) as db, db:
                rows=db.execute("SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation",
                    (value["operation_prefix"]+"%",)).fetchall()
            if (len(rows)!=value["previous_requests"] or hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()!=value["previous_rows_sha256"]):
                raise ValueError("original_dispatch_rows_changed")
            if "preserved_operations" in value and value["preserved_operations"]!=[row[0] for row in rows]:
                raise ValueError("preserved_operation_catalog_changed")
            if extra:
                account=value.get("supplemental_account_id","")
                if not isinstance(account,str) or len(account)!=64 or any(c not in "0123456789abcdef" for c in account):
                    raise ValueError("supplemental_account_identity_invalid")
                supplement_path=ledger.parent/"supplements"/account/"dispatch.sqlite"
                if not supplement_path.exists(): raise ValueError("original_supplement_ledger_missing")
                with closing(sqlite3.connect(supplement_path)) as db, db:
                    previous=db.execute("SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation",
                        (value["operation_prefix"]+"%",)).fetchall()
                if (len(previous)!=value["supplemental_previous_requests"] or hashlib.sha256(
                        json.dumps(previous,sort_keys=True).encode()).hexdigest()!=value["supplemental_previous_rows_sha256"]):
                    raise ValueError("original_supplement_dispatch_rows_changed")
            if continuation:
                account=value.get("extra_supplemental_account_id","")
                if not isinstance(account,str) or len(account)!=64 or any(c not in "0123456789abcdef" for c in account):
                    raise ValueError("extra_account_identity_invalid")
                extra_path=ledger.parent/"extra-supplements"/account/"dispatch.sqlite"
                if not extra_path.exists(): raise ValueError("original_extra_ledger_missing")
                with closing(sqlite3.connect(extra_path)) as db, db:
                    previous=db.execute("SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation",
                        (value["operation_prefix"]+"%",)).fetchall()
                if (len(previous)!=value["extra_previous_requests"] or hashlib.sha256(
                        json.dumps(previous,sort_keys=True).encode()).hexdigest()!=value["extra_previous_rows_sha256"]):
                    raise ValueError("original_extra_dispatch_rows_changed")
                if value.get("continuation_account_id"):
                    account=value["continuation_account_id"]
                    if len(account)!=64 or any(c not in "0123456789abcdef" for c in account):
                        raise ValueError("continuation_identity_invalid")
                    path=ledger.parent/"continuations"/account/"dispatch.sqlite"
                    if not path.exists(): raise ValueError("original_continuation_ledger_missing")
                    with closing(sqlite3.connect(path)) as db, db:
                        previous=db.execute("SELECT * FROM dispatches WHERE operation NOT LIKE ? ORDER BY operation",
                            (value["operation_prefix"]+"%",)).fetchall()
                    if (len(previous)!=value["continuation_previous_requests"] or hashlib.sha256(
                            json.dumps(previous,sort_keys=True).encode()).hexdigest()!=value["continuation_previous_rows_sha256"]):
                        raise ValueError("original_continuation_rows_changed")
            if value.get("unknown_dispatch_adjudication"):
                expected={"operations":[row[0] for row in rows if row[4]!="http_complete"],
                    "counted_in_budget":True,"usage_remains_unknown":True,"original_operations_non_replayable":True,
                    "disposition":"preserved_unknown_new_explicitly_authorized_complete_run"}
                if (value["unknown_dispatch_adjudication"]!=expected or approved.get("resume_counted_unknown_confirmed") is not True
                        or value.get("max_planned_new_requests",123)+len(rows)>value["max_http_requests"]+(supplement or {}).get("max_requests",0)+(extra or {}).get("max_requests",0)+(continuation or {}).get("max_requests",0)):
                    raise ValueError("unknown_dispatch_adjudication_or_resume_budget_invalid")
    return value


async def evaluate(plan, dataset, output, *, dry, prepared_path=None, authorization_path=None):
    import httpx
    from openai import AsyncOpenAI
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.config.config import settings
    from app.models.models import Base,User
    from app.services.llm_service import LLMService
    from app.services.model_gateway import model_context
    from app.agent.request_understanding import understand
    from app.agent.validation_llm import LLMRAGJudge
    from app.agent.intent_items import REVIEW_CONTEXT,snapshot,verify_review
    from scripts.m5_live_budget import DispatchLedger,OPERATION,guarded_transport

    cases={case["case_id"]:case for case in dataset["cases"]}
    if dry: settings.LLM_API_KEY="synthetic-rehearsal-only"
    if not dry and not settings.LLM_API_KEY: raise ValueError("provider_key_missing")
    engine=create_async_engine(settings.DATABASE_URL)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(User(id=101,username="synthetic-semantic-evaluation",role="user",status=True,token_version=0))
        await db.commit()
    temporary=tempfile.TemporaryDirectory(prefix="item-dry-ledger-") if dry else None
    phase_id=plan.get("phase_prepared_id",plan["prepared_id"])
    directory=Path(temporary.name) if dry else ROOT/"reports/multi-intent/live-budget"/phase_id
    limits={"max_requests":plan["max_http_requests"],"max_input_tokens":plan["max_input_bytes_reserved"],
            "max_output_tokens":plan["max_output_tokens_reserved"]}
    operations=operation_limits(dataset,plan.get("operation_prefix",""),
        end_to_end_limit=plan["max_end_to_end_http_per_case"],historical_prefixes=plan.get("historical_operation_prefixes",[]),
        preserved_operations=plan.get("preserved_operations",[]))
    ledger=DispatchLedger(directory/"dispatch.sqlite",phase_id,limits,operations)
    if plan.get("supplemental_limits"):
        account=plan.get("supplemental_account_id",plan["prepared_id"])
        supplement=DispatchLedger(directory/"supplements"/account/"dispatch.sqlite",account,
            plan["supplemental_limits"],operations)
        ledger=TwoAccountLedger(ledger,supplement)
    if plan.get("extra_supplemental_limits"):
        extra_account=plan.get("extra_supplemental_account_id",plan["prepared_id"])
        extra=DispatchLedger(directory/"extra-supplements"/extra_account/"dispatch.sqlite",extra_account,
            plan["extra_supplemental_limits"],operations)
        ledger=TwoAccountLedger(ledger,extra)
    if plan.get("continuation_limits"):
        continuation_account=plan.get("continuation_account_id",plan["prepared_id"])
        continuation=DispatchLedger(directory/"continuations"/continuation_account/"dispatch.sqlite",continuation_account,
            plan["continuation_limits"],operations)
        ledger=TwoAccountLedger(ledger,continuation)
    if plan.get('final_supplemental_limits'):
        account=plan.get('final_supplemental_account_id',plan['prepared_id'])
        final=DispatchLedger(directory/'final-supplements'/account/'dispatch.sqlite',account,
            plan['final_supplemental_limits'],operations)
        ledger=TwoAccountLedger(ledger,final)
    if plan.get('layout_supplemental_limits'):
        layout=DispatchLedger(directory/'layout-supplements'/plan['prepared_id']/'dispatch.sqlite',plan['prepared_id'],
            plan['layout_supplemental_limits'],operations)
        ledger=TwoAccountLedger(ledger,layout)
    initial_requests=ledger.snapshot()["requests"]
    graph_counters={}
    captures=[]
    timings=[]

    def simulated(request):
        case_id,stage=ACTIVE.get()
        case=cases[case_id]
        payload=json.loads(request.content)
        function=(payload.get("tools") or [{}])[0].get("function",{}).get("name","")
        state=fixture_state(case)
        if "Understanding" in function:
            value=state.understanding.model_dump()
            if function=="ItemizedTaskUnderstanding": value.pop("task_kind",None)
        elif "Judge" in function:
            bad=stage=="bad"
            if stage=="graph": bad=False
            _,checks=candidate(case,bad=bad)
            if bad: checks[case["bad_item"]].update(supported=False,conditions_preserved=False)
            value={"grounded":not bad,"fact_score":0.0 if bad else 1.0,"has_unsupported_claim":bad,
                "refuse_recommended":bad,"reason":"synthetic_contract_fixture","item_checks":checks}
        elif "TaskDecision" in function:
            content=payload["messages"][-1]["content"]
            observed=json.loads(content)
            value={"step":{"action":"search_knowledge","knowledge_focus":"request"}} if "search_knowledge" in observed["allowed_actions"] else {"step":{"action":"finish"}}
        elif function=="LLMResponse":
            value={"reply":candidate(case)[0],"intent":state.detected_intent,"confidence":1.0,"citations":[],"need_human":False}
        else:
            value=candidate(case)[0]
        message={"role":"assistant","content":value if isinstance(value,str) else None}
        if not isinstance(value,str):
            message["tool_calls"]=[{"id":"synthetic-call","type":"function","function":{"name":function,"arguments":json.dumps(value,ensure_ascii=False)}}]
        return httpx.Response(200,json={"id":"synthetic","object":"chat.completion","created":1,"model":settings.LLM_MODEL,
            "choices":[{"index":0,"message":message,"finish_reason":"stop" if isinstance(value,str) else "tool_calls"}],
            "usage":{"prompt_tokens":100,"completion_tokens":30,"total_tokens":130}},request=request)

    guarded=guarded_transport(ledger,model=settings.LLM_MODEL,endpoint=settings.LLM_API_BASE,
        underlying=httpx.MockTransport(simulated) if dry else None)

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            case_id,stage=ACTIVE.get()
            if stage=="graph":
                graph_counters[case_id]=graph_counters.get(case_id,0)+1
                limit=3 if plan.get("carried_evidence") and case_id in {"MI-04","MI-06"} else plan["max_end_to_end_http_per_case"]
                limit=plan.get('graph_http_limits',{}).get(case_id,limit)
                if graph_counters[case_id]>limit: raise ValueError("fixed_end_to_end_request_cap")
                operation=f"E2E-{case_id}/http-{graph_counters[case_id]}"
            else: operation=f"{case_id}/{stage}"
            operation=plan.get("operation_prefix","")+operation
            started=time.perf_counter()
            timing={"operation":operation,"request_bytes":len(request.content),"events":[],"outcome":"dispatching"}
            timings.append(timing)
            async def trace(name, info):
                timing["events"].append({"stage":name,"elapsed_ms":round((time.perf_counter()-started)*1000,2)})
            request.extensions["trace"]=trace
            if not dry: validated_plan(prepared_path,authorization_path)
            if not dry and ledger.snapshot()["requests"]-initial_requests>=plan.get("max_planned_new_requests",72):
                raise ValueError("fixed_run_request_cap")
            token=OPERATION.set(operation)
            try:
                response=await guarded.handle_async_request(request)
                captures.append({"operation":operation,"status":response.status_code,"response":response.json()})
                save(output/"responses.json",captures)
                timing["outcome"]="http_complete"
                return response
            finally:
                timing["elapsed_ms"]=round((time.perf_counter()-started)*1000,2)
                if timing["outcome"]=="dispatching": timing["outcome"]="cancelled_or_failed"
                save(output/"http-timings.json",timings)
                OPERATION.reset(token)
        async def aclose(self): await guarded.aclose()

    async with AsyncExitStack() as resources:
        provider=await resources.enter_async_context(httpx.AsyncClient(transport=Transport(),timeout=30,trust_env=False))
        original=LLMService._get_chat_model
        def model(self, config, **kwargs):
            value=original(self,config,**kwargs)
            sdk=AsyncOpenAI(api_key=config.api_key,base_url=config.api_base,http_client=provider,max_retries=0)
            value.root_async_client,value.async_client=sdk,sdk.chat.completions
            return value
        resources.enter_context(patch.object(LLMService,"_get_chat_model",model))
        service=LLMService()
        resources.push_async_callback(service.close)
        rows=[]
        carried=json.loads((ROOT/plan["carried_evidence"]["path"]).read_bytes()) if plan.get("carried_evidence") and not dry else {}
        for case in dataset["cases"]:
            inherited=carried.get("complete_cases",{}).get(case["case_id"])
            if inherited:
                rows.append(inherited)
                save(output/"component-results.json",rows)
                continue
            state=fixture_state(case)
            from app.agent.state import AgentState
            request_state=AgentState(session_id="parse-"+case["case_id"],user_id=101,actor_id=101,source_message_id=17,
                user_message=case["message"],understanding_mode="model",task_loop_enabled=True)
            partial=carried.get("partial_cases",{}).get(case["case_id"],{})
            if partial:
                plan_score=partial["plan"]
            else:
                token=ACTIVE.set((case["case_id"],"understanding"))
                try:
                    async with model_context(101,"understand-"+case["case_id"],factory=factory):
                        parsed=await understand(service,request_state)
                finally: ACTIVE.reset(token)
                plan_score=score_plan(case,parsed)
            verdicts=[]
            for bad in (False,True):
                if bad and partial.get("bad_judgement"):
                    verdicts.append(partial["bad_judgement"])
                    continue
                body,_=candidate(case,bad=bad)
                token=ACTIVE.set((case["case_id"],"bad" if bad else "good"))
                context_token=REVIEW_CONTEXT.set(snapshot(state))
                try:
                    async with model_context(101,f'judge-{case["case_id"]}-{bad}',factory=factory):
                        from app.agent.answer_contracts import review_query,tool_evidence_text
                        judgement=await LLMRAGJudge(service).judge(body,review_query(state),state.current_evidence(),tool_evidence_text(state))
                finally:
                    REVIEW_CONTEXT.reset(context_token); ACTIVE.reset(token)
                _,issues=verify_review(state,body,judgement.get("item_checks"))
                valid=bool(isinstance(judgement.get("item_checks"),list))
                accepted=judgement["publish_allowed"] and not issues
                oracle_score=None
                if dataset.get('oracle_version')=='multi-intent.oracle.v2' and not bad:
                    from scripts.multi_intent_oracle import score
                    oracle_score=score(case,protocol_valid=valid,response_supported=bool(judgement['publish_allowed']),
                        item_checks=judgement.get('item_checks'),binding_issues=issues)
                    accepted=accepted and oracle_score['passed']
                verdicts.append({"bad":bad,"accepted":accepted,"valid_output":valid,"issues":issues,"judgement":judgement,
                    **({'oracle_score':oracle_score} if oracle_score else {})})
            row={"case_id":case["case_id"],"split":case["split"],"plan":plan_score,"judgements":verdicts}
            rows.append(row); save(output/"component-results.json",rows)
            if (not plan_score["all_items_covered"] or not verdicts[0]["accepted"]
                    or verdicts[1]["accepted"] or not all(result["valid_output"] for result in verdicts)):
                break
        good=sum(row["judgements"][0]["accepted"] for row in rows)
        false_accepts=sum(row["judgements"][1]["accepted"] for row in rows)
        full_plans=sum(row["plan"]["all_items_covered"] for row in rows)
        valid=sum(result["valid_output"] for row in rows for result in row["judgements"])
        gates=len(rows)==len(dataset["cases"]) and good==len(rows) and false_accepts==0 and full_plans==len(rows) and valid==2*len(rows)
        end_to_end=[]
        if gates:
            from app.agent.graph import AgentGraph
            from app.agent.state import AgentState
            from app.agent.tools import ToolExecutionResult
            for case_id in plan["end_to_end_cases"]:
                inherited_graph=carried.get('full_graph_cases',{}).get(case_id)
                if inherited_graph:
                    end_to_end.append(inherited_graph)
                    save(output/'end-to-end-results.json',end_to_end)
                    continue
                case=cases[case_id]
                class Knowledge:
                    def evidence_is_current(self,items): return all(item.get("version_id")=="synthetic-v1" for item in items)
                    async def async_search(self,query,top_k):
                        gold=fixture_state(case)
                        selected=[entry for index,content in enumerate(case["knowledge"]) if content and case["items"][index]["text"] in query
                                  for entry in gold.current_evidence() if entry["id"].endswith(f"-i{index+1}")]
                        return {"success":True,"results":selected,"meta":{"score_type":"rrf","knowledge_epoch":1},
                            "evidence_bundle":{"schema_version":1,"items":selected,"knowledge_epoch":1}}
                async def read(tasks,principal):
                    if principal.user_id!=101: raise ValueError("synthetic_principal_mismatch")
                    result=[]
                    for task in tasks:
                        if task.tool!="query_order" or task.resource_id!=case["order_id"]: raise ValueError("read_outside_fixed_case")
                        result.append(({"user_id":101,"order_id":task.resource_id},ToolExecutionResult(success=True,data={
                            "order_id":task.resource_id,"status_code":"delivered","status":"已签收","total_amount":"99.10"})))
                    return result
                with patch("app.agent.graph.get_knowledge_base",lambda:Knowledge()),patch("app.agent.read_plan.run_authorized_reads",read):
                    graph=AgentGraph()
                    resources.push_async_callback(graph.llm_service.close)
                    state=AgentState(session_id="e2e-"+case_id,user_id=101,actor_id=101,source_message_id=17,
                        user_message=case["message"],understanding_mode="model",task_loop_enabled=True)
                    token=ACTIVE.set((case_id,"graph"))
                    try:
                        async with model_context(101,"e2e-"+case_id,factory=factory):
                            result=await graph.run(state)
                    finally:
                        ACTIVE.reset(token)
                expected="waiting_input" if case_id in {"MI-04","MI-06"} else "completed"
                oracle_score=None
                graph_passed=bool(result.task and result.task.status==expected and result.review_result and result.review_result.passed)
                if dataset.get('oracle_version')=='multi-intent.oracle.v2':
                    from scripts.multi_intent_oracle import score
                    oracle_score=score(case,protocol_valid=bool(result.review_result and result.review_result.status!='unknown'),
                        response_supported=bool(result.review_result and result.review_result.passed),
                        item_checks=[r.model_dump() for r in result.task_runtime.item_reviews] if result.task_runtime else None,
                        task_status=result.task.status if result.task else None,require_task=True)
                    graph_passed=oracle_score['passed']
                end_to_end.append({"case_id":case_id,"passed":graph_passed,
                    "status":result.task.status if result.task else None,"reply":result.reply,
                    "item_outcomes":result.task.item_outcomes if result.task else {},
                    **({'oracle_score':oracle_score} if oracle_score else {})})
                save(output/"end-to-end-results.json",end_to_end)
                if not end_to_end[-1]["passed"]:
                    break
        report={"mode":"sdk_rehearsal" if dry else "real_provider_semantic_evaluation","passed":gates and len(end_to_end)==4 and all(row["passed"] for row in end_to_end),
            "cases":len(rows),"good_accepts":good,"false_accepts":false_accepts,"valid_judge_outputs":valid,
            "fully_covered_plans":full_plans,"end_to_end":end_to_end,"budget":ledger.snapshot(),
            "paid_http_requests":0 if dry else ledger.snapshot()["requests"]-initial_requests,
            "phase_requests_before":initial_requests,"new_dispatches":ledger.snapshot()["requests"]-initial_requests,
            "label_origin":dataset["review_origin"],
            "semantic_model_quality":"NOT_RUN" if dry else "MEASURED_ON_FROZEN_SYNTHETIC_CASES_NOT_HUMAN_GOLD"}
        report.update(planned_cases=len(dataset["cases"]),not_run_cases=[case["case_id"] for case in dataset["cases"][len(rows):]],
            generated_semantic_review="FIXTURE_ONLY" if dry else "REQUIRES_SEPARATE_REVIEW_OF_GENERATED_TEXT",
            final_admission_allowed=False)
        report["evidence_mode"]="SEGMENTED_EQUIVALENT_INPUT_CONTRACTS_NOT_SINGLE_SOURCE_FULL_RUN" if carried else "SINGLE_FROZEN_SOURCE_RUN"
        report["carried_case_ids"]=list(carried.get("complete_cases",{}))
        report["carried_partial_case_ids"]=list(carried.get("partial_cases",{}))
        report['carried_full_graph_ids']=list(carried.get('full_graph_cases',{}))
        report["split_metrics"]={split:{"cases_run":len(selected),
            "good_accepts":sum(row["judgements"][0]["accepted"] for row in selected),
            "bad_false_accepts":sum(row["judgements"][1]["accepted"] for row in selected),
            "all_items_covered":sum(row["plan"]["all_items_covered"] for row in selected)}
            for split in ("development","holdout") if (selected:=[row for row in rows if row["split"]==split])}
        from scripts.test_provenance import snapshot_sources
        after=snapshot_sources(ROOT)
        report["source_tree_stable"]=after["fingerprint"]==plan["source_fingerprint"]
        if not report["source_tree_stable"]: report["passed"]=False
        save(output/"report.json",report)
    await engine.dispose()
    if temporary: temporary.cleanup()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared",type=Path,required=True)
    parser.add_argument("--authorization",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--rehearse",action="store_true")
    args=parser.parse_args()
    args.prepared=args.prepared.resolve()
    if args.authorization: args.authorization=args.authorization.resolve()
    args.output=args.output.resolve()
    if args.output.exists(): raise SystemExit("refusing_to_overwrite_evidence")
    # Validate before credentials or provider initialization.
    plan=validated_plan(args.prepared,args.authorization,rehearse=args.rehearse)
    blocked=[]
    if args.rehearse:
        local=threading.local()
        original_pair=socket.socketpair
        def pair(*positional,**keywords):
            local.pair=True
            try: return original_pair(*positional,**keywords)
            finally: local.pair=False
        socket.socketpair=pair
        def deny(event,values):
            if event in {"socket.connect","socket.getaddrinfo","socket.sendto"} and not getattr(local,"pair",False):
                blocked.append(event)
                raise RuntimeError("sdk_rehearsal_network_blocked")
        sys.addaudithook(deny)
    with isolated_configuration(live=not args.rehearse):
        result=asyncio.run(evaluate(plan,json.loads(DATA.read_bytes()),args.output,dry=args.rehearse,
            prepared_path=args.prepared,authorization_path=args.authorization))
    if args.rehearse:
        result["blocked_network_operations"]=len(blocked)
        save(args.output/"report.json",result)
    print(json.dumps({"passed":result["passed"],"paid_http_requests":result["paid_http_requests"],
                      "semantic_model_quality":result["semantic_model_quality"]}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(json.dumps({"status":"FAILED_CLOSED","error_type":type(exc).__name__}))
        raise SystemExit(1)
