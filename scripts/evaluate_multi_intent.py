"""冻结合成语义材料及逐项契约评测；离线结果不冒充真实模型语义质量。"""
from __future__ import annotations
from contextlib import closing

import argparse
import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "acceptance/multi-intent/scenarios-v1.json"


def provider_stamp() -> dict:
    from dotenv import get_key
    defaults={"LLM_API_BASE":"https://token.uino.com/v1","LLM_MODEL":"deepseek-flash",
        "LLM_MODEL_VERSION":"deepseek-flash","LLM_THINKING_MODE":"disabled","LLM_STRUCTURED_OUTPUT_METHOD":"function_calling"}
    return {key:os.environ.get(key) or get_key(ROOT/".env",key) or value for key,value in defaults.items()}


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


@contextmanager
def isolated_configuration(*, live=False):
    """配置在任何app导入前隔离；不连接业务数据库/Redis/向量或外部通知。"""
    from dotenv import get_key
    names = [node.target.id for cls in ast.parse((ROOT/"app/config/config.py").read_text(encoding="utf-8-sig")).body
        if isinstance(cls, ast.ClassDef) and cls.name == "Settings" for node in cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)]
    old = {key: os.environ.get(key) for key in names}
    provider_keys = ["LLM_API_BASE", "LLM_MODEL", "LLM_MODEL_VERSION", "LLM_API_KEY", "LLM_THINKING_MODE", "LLM_STRUCTURED_OUTPUT_METHOD"]
    provider = {key: os.environ.get(key) or (get_key(ROOT/".env", key) if live else None) for key in provider_keys}
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="aicshub-item-eval-") as directory:
        try:
            for name in names: os.environ.pop(name, None)
            os.chdir(directory)
            os.environ.update({"DATABASE_URL_OVERRIDE":f"sqlite+aiosqlite:///{Path(directory).as_posix()}/evaluation.db",
                "REDIS_ENABLED":"false", "USE_MILVUS":"false", "AUTH_DEMO_ACCOUNTS_ENABLED":"false",
                "USER_MEMORY_ENABLED":"false", "LANGFUSE_ENABLED":"false", "MCP_ENABLED":"false",
                "REQUEST_UNDERSTANDING_MODE":"model", "AGENT_TASK_LOOP_ENABLED":"true", "AGENT_ITEM_COMPLETION_ENABLED":"true",
                "LLM_GATEWAY_ENABLED":"true",
                "LLM_JUDGE_CONTRACT_VERSION":"v1", "LLM_MAX_TOKENS":"1024", "LLM_MAX_RETRIES":"0",
                "LLM_DAILY_USER_TOKEN_BUDGET":"5000000", "LLM_DAILY_USER_COST_USD":"100", "LLM_MAX_COST_USD":"0.2",
                "LLM_API_KEY":"", "LLM_API_BASE":"http://offline.invalid/v1", "LLM_MODEL":"deepseek-flash",
                "LLM_MODEL_VERSION":"deepseek-flash", "LLM_ROUTE_VERSION":"understanding.items.v1",
                "LLM_THINKING_MODE":"disabled", "LLM_STRUCTURED_OUTPUT_METHOD":"function_calling"})
            if live:
                os.environ.update({key:value for key,value in provider.items() if value is not None})
            yield
        finally:
            os.chdir(original)
            for name in names:
                os.environ.pop(name, None)
                if old[name] is not None: os.environ[name] = old[name]


def fixture_state(case: dict):
    """提供明确标记的合成当前观察，供Judge质量测量；不证明业务工具真实执行。"""
    from app.agent.request_understanding import ItemizedTaskUnderstanding, route_understanding, EvidenceBundle
    from app.agent.state import AgentState
    from app.agent.task_loop import enter_task
    from app.agent.intent_items import catalog, observe, check_conditions, evidence_digest
    has_order = any(item["kind"] == "query_order" for item in case["items"])
    requests=[]
    for item in case["items"]:
        kind,text=item["kind"],item["text"]
        topic=("query_order" if kind=="query_order" else "refund" if kind=="prepare_refund" else
               "complaint" if kind=="unsupported" else "promotion" if "优惠" in text else
               "technical" if kind=="troubleshoot" or any(word in text for word in ["保修","进水","耳机"]) else "refund")
        request_type="query" if kind=="query_order" else "apply" if kind in {"prepare_refund","unsupported"} else "inquiry"
        entry={"topic":topic,"request_type":request_type}
        if entry not in requests: requests.append(entry)
    fields = dict(**requests[0],
        entities={"order_id":case["order_id"],"product":None}, conditions=[{"check":code,"source":"current_message"} for code in case["conditions"]],
        subrequests=requests[:3],
        service_preference="unspecified",emotion="neutral",clarification=None,retrieval_query=case["message"][:300],
        entity_source="current_message" if case["order_id"] else "none",
        read_tasks=[{"tool":"query_order","resource_id":case["order_id"]}] if has_order and case["order_id"] else [],
        parameters={"reason":"外壳明显破损","refund_type":"refund"} if "外壳明显破损" in case["message"] else {},
        missing_information=["order_id"] if has_order and not case["order_id"] else [],
        task_kind="multi",continue_task=False,itemization_version="intent-items.v1",items=case["items"])
    value = ItemizedTaskUnderstanding.model_validate(fields)
    state = AgentState(session_id="eval-"+case["case_id"],user_id=101,actor_id=101,source_message_id=17,
        user_message=case["message"],understanding_mode="model",task_loop_enabled=True)
    route = route_understanding(state,value)
    enter_task(state,route)
    if has_order and case["order_id"]:
        success = not case["order_denied"]
        data = {"order_id":case["order_id"],"status_code":case["order_status"],
            "status":"已签收" if case["order_status"]=="delivered" else "待发货","total_amount":"99.10"}
        result = {"success":success,"data":data if success else None,"error_type":None if success else "permission"}
        state.record_tool_observation("query_order",result)
        state.add_tool_call("query_order",{"user_id":101,"order_id":case["order_id"]},result,success)
        refs = [entry["digest"] for entry in catalog(state).values() if entry["kind"]=="query_order"]
        for item in state.task.items:
            if item.kind=="query_order": observe(state,item.item_id,refs,None if success else "read_failed")
    check_conditions(state)
    evidence = []
    for index,content in enumerate(case["knowledge"]):
        if content:
            evidence.append({"id":case["case_id"]+f"-i{index+1}","version_id":"synthetic-v1","title":"合成评测政策","content":content})
    state.accept_retrieval({"success":True,"results":evidence,"meta":{"knowledge_epoch":1,"score_type":"rrf"},
        "evidence_bundle":EvidenceBundle(items=evidence,knowledge_epoch=1).model_dump()})
    for index,item in enumerate(state.task.items):
        if item.kind in {"knowledge","troubleshoot"}:
            refs=[evidence_digest(entry) for entry in evidence if entry["id"]==case["case_id"]+f"-i{index+1}"]
            if item.kind=="troubleshoot": refs += [entry["digest"] for entry in catalog(state).values() if entry["kind"]=="current_user_report"]
            observe(state,item.item_id,refs,None if case["knowledge"][index] else "no_item_evidence")
    if case["preview"]:
        reason=state.current_business_parameters().reason or "合成测试原因"
        payload={"user_id":101,"session_id":state.session_id,"order_id":case["order_id"],"reason":reason,"type":"refund",
                 "action_id":"synthetic-preview","action_version":1}
        state.record_refund_action({"action_id":"synthetic-preview","version":1,"order_id":case["order_id"],"reason":reason,
            "type":"refund","amount":"99.10","currency":"CNY","status":"pending","expires_at":"2099-01-01T00:00:00+00:00","is_simulated":True})
        state.set_refund_preview(payload)
        refs=[entry["digest"] for entry in catalog(state).values() if entry["kind"]=="prepare_refund"]
        for item in state.task.items:
            if item.kind=="prepare_refund": observe(state,item.item_id,refs)
    return state


def candidate(case: dict, *, bad=False) -> tuple[str, list[dict]]:
    from app.agent.intent_items import snapshot
    state = fixture_state(case)
    context = snapshot(state)
    answers = list(case["good_answers"])
    if bad: answers[case["bad_item"]]=case["bad_answer"]
    statuses=case["good_statuses"] or ["answered"]*len(answers)
    checks=[]
    for index,(item,answer) in enumerate(zip(context["items"],answers)):
        status=statuses[index]
        references = [ref for ref in item["evidence_ids"] if status != "waiting_feedback" or ref.startswith("K")]
        checks.append({"item_id":item["item_id"],"status":status,"answer_quote":answer,
            "evidence_ids":references[:1],"supported":True,"conditions_preserved":True,
            "answerability":case.get('reference_answerability',[])[index] if case.get('reference_answerability') else
                "partial" if status in {"waiting_feedback","awaiting_confirmation"} else "answerable" if status=="answered" else "unanswerable"})
    body="\n".join(f"【i{index+1}】{answer}" for index,answer in enumerate(answers) if answer)
    if bad and case["attack"]=="foreign_reference":
        checks[case["bad_item"]]["evidence_ids"]=context["items"][0]["evidence_ids"]
    return body,checks


def score_plan(case: dict, parsed) -> dict:
    items = getattr(parsed,"items",[]) if parsed is not None else []
    correct=0
    for expected,actual in zip(case["items"],items):
        text=actual.text
        if actual.kind==expected["kind"] and (text==expected["text"] or len(text)>=2 and text in expected["text"]):
            correct+=1
    return {"expected_items":len(case["items"]),"covered_items":correct,
        "all_items_covered":correct==len(case["items"])==len(items)}


def offline(dataset: dict) -> dict:
    from app.agent.intent_items import verify_review
    rows=[]
    for case in dataset["cases"]:
        state=fixture_state(case)
        good,good_checks=candidate(case)
        _,good_issues=verify_review(state,good,good_checks)
        bad,lying=candidate(case,bad=True)
        oracle=deepcopy(lying)
        oracle[case["bad_item"]].update(supported=False,conditions_preserved=False)
        _,oracle_issues=verify_review(state,bad,oracle)
        _,lying_issues=verify_review(state,bad,lying)
        rows.append({"case_id":case["case_id"],"split":case["split"],"fixture_good_accepted":not good_issues,
            "fixture_bad_rejected":bool(oracle_issues),"good_issues":good_issues,
            "bad_accepted_if_judge_lies":not lying_issues})
    return {"mode":"offline_contract_replay","cases":rows,"passed":all(row["fixture_good_accepted"] and row["fixture_bad_rejected"] for row in rows),
        "semantic_model_quality":"NOT_RUN","label_origin":dataset["review_origin"],"provider_http_requests":0,
        "semantic_dependence_demonstrated":sum(row["bad_accepted_if_judge_lies"] for row in rows),
        "limitations":"手工Judge回执只验证程序契约；正常结构不能证明语义正确。真实误放/误拒须用独立模型输出对冻结标签评分。"}


def prepare(output: Path, dataset: dict, *, provider: dict | None = None, revalidate: Path | None = None,
            resume_counted_unknown: bool = False, continuation_prefix: str = "revision-v3/",
            end_to_end_limit: int = 5, supplemental_requests: int = 0,
            extra_supplemental_requests: int = 0, continuation_requests: int = 0,
            carried_evidence: Path | None = None) -> dict:
    from scripts.test_provenance import snapshot_sources
    source=snapshot_sources(ROOT)
    plan={"plan_id":"multi-intent-semantic-v1","status":"PROPOSED_NOT_AUTHORIZED","cases":len(dataset["cases"]),
        "component_http_requests":48,"end_to_end_cases":["MI-01","MI-04","MI-06","MI-16"],
        "max_end_to_end_http_per_case":6,"max_http_requests":80,"max_input_bytes_reserved":80*24576,
        "max_output_tokens_reserved":80*1024,"old_phase_requests":111,"old_phase_limit":120,
        "old_remaining_not_reused":True,"dataset_sha256":hashlib.sha256(DATA.read_bytes()).hexdigest(),
        "source_fingerprint":source["fingerprint"],"provider":provider or provider_stamp(),"stop_on_first_case_failure":True,
        "gates":{"critical_false_accepts":0,"all_items_covered_rate":1.,
        "good_candidate_acceptance_rate":1.,"valid_output_rate":1.},"label_origin":dataset["review_origin"]}
    if supplemental_requests:
        if supplemental_requests!=24 or not resume_counted_unknown or revalidate is None:
            raise ValueError("supplement_requires_separate_explicit_24_request_authorization")
        plan["supplemental_limits"]={"max_requests":24,"max_input_tokens":24*24576,"max_output_tokens":24*1024}
    if extra_supplemental_requests:
        if extra_supplemental_requests!=12 or supplemental_requests!=24:
            raise ValueError("extra_supplement_requires_separate_12_request_authorization")
        plan["extra_supplemental_limits"]={"max_requests":12,"max_input_tokens":12*24576,"max_output_tokens":12*1024}
    if continuation_requests:
        if continuation_requests!=6 or extra_supplemental_requests!=12:
            raise ValueError("continuation_requires_separate_6_request_authorization")
        plan["continuation_limits"]={"max_requests":6,"max_input_tokens":6*24576,"max_output_tokens":6*1024}
    planned_requests=48+4*end_to_end_limit
    if carried_evidence:
        carry=json.loads(carried_evidence.read_bytes())
        if (not resume_counted_unknown or carry.get("approved_segmented_resume") is not True
                or carry.get("unchanged_projection_verified") is not True or carry.get("new_request_cap")!=42
                or carry.get("dataset_sha256")!=plan["dataset_sha256"]):
            raise ValueError("segmented_evidence_invalid")
        planned_requests=42
        plan["carried_evidence"]={"path":str(carried_evidence.resolve().relative_to(ROOT)).replace('\\','/'),
            "sha256":hashlib.sha256(carried_evidence.read_bytes()).hexdigest()}
    if revalidate is not None:
        import sqlite3
        original=json.loads(revalidate.read_bytes())
        phase=original.get("phase_prepared_id",original["prepared_id"])
        ledger=ROOT/"reports/multi-intent/live-budget"/phase/"dispatch.sqlite"
        if not ledger.exists(): raise ValueError("original_phase_ledger_required")
        with closing(sqlite3.connect(ledger)) as db, db:
            rows=db.execute("SELECT * FROM dispatches ORDER BY operation").fetchall()
        unknown=[row[0] for row in rows if row[4]!="http_complete"]
        if unknown and not resume_counted_unknown: raise ValueError("unknown_dispatch_requires_adjudication")
        if resume_counted_unknown:
            import re
            if (not re.fullmatch(r"revision-v[3-9][0-9]*/",continuation_prefix)
                    or type(end_to_end_limit) is not int or not 1 <= end_to_end_limit <= 5
                    or any(row[0].startswith(continuation_prefix) for row in rows)
                    or any(row[4] not in {"http_complete","http_complete_usage_unknown","unknown_dispatched"} for row in rows)):
                raise ValueError("resume_requires_unused_prefix_and_preservable_history")
            if planned_requests+len(rows)>plan["max_http_requests"]+supplemental_requests+extra_supplemental_requests+continuation_requests:
                raise ValueError("complete_resume_exceeds_remaining_phase_budget")
        if original["dataset_sha256"]!=plan["dataset_sha256"] or original["provider"]!=plan["provider"]:
            raise ValueError("revalidation_cannot_replace_frozen_material_or_provider")
        plan.update(phase_prepared_id=phase,operation_prefix="revision-v2/",previous_requests=len(rows),
            max_additional_requests=plan["max_http_requests"]+supplemental_requests+extra_supplemental_requests+continuation_requests-len(rows),
            previous_rows_sha256=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),
            revalidation_of=original["prepared_id"],separate_revalidation_approval_required=True)
        if resume_counted_unknown:
            plan.update(operation_prefix=continuation_prefix, preserved_operations=[row[0] for row in rows],
                max_end_to_end_http_per_case=end_to_end_limit, max_planned_new_requests=planned_requests,
                unknown_dispatch_adjudication={"operations":unknown,"counted_in_budget":True,
                    "usage_remains_unknown":True,"original_operations_non_replayable":True,
                    "disposition":"preserved_unknown_new_explicitly_authorized_complete_run"})
        if extra_supplemental_requests:
            if original.get("supplemental_limits")!=plan["supplemental_limits"]:
                raise ValueError("previous_supplement_authorization_required")
            account=original.get("supplemental_account_id",original["prepared_id"])
            supplement_path=ledger.parent/"supplements"/account/"dispatch.sqlite"
            if not supplement_path.exists(): raise ValueError("previous_supplement_ledger_required")
            with closing(sqlite3.connect(supplement_path)) as db, db:
                prior=db.execute("SELECT * FROM dispatches ORDER BY operation").fetchall()
            if prior: raise ValueError("previous_supplement_must_be_preserved_unused")
            plan.update(supplemental_account_id=account,supplemental_previous_requests=0,
                supplemental_previous_rows_sha256=hashlib.sha256(json.dumps(prior,sort_keys=True).encode()).hexdigest())
        if continuation_requests:
            if original.get("extra_supplemental_limits")!=plan["extra_supplemental_limits"]:
                raise ValueError("previous_extra_account_authorization_required")
            extra_account=original.get("extra_supplemental_account_id",original["prepared_id"])
            extra_path=ledger.parent/"extra-supplements"/extra_account/"dispatch.sqlite"
            if not extra_path.exists(): raise ValueError("previous_extra_account_ledger_required")
            with closing(sqlite3.connect(extra_path)) as db, db:
                extra_rows=db.execute("SELECT * FROM dispatches ORDER BY operation").fetchall()
            if extra_rows: raise ValueError("previous_extra_account_must_be_preserved_unused")
            plan.update(extra_supplemental_account_id=extra_account,extra_previous_requests=0,
                extra_previous_rows_sha256=hashlib.sha256(json.dumps(extra_rows,sort_keys=True).encode()).hexdigest())
            if carried_evidence:
                continuation_account=original.get("continuation_account_id",original["prepared_id"])
                continuation_path=ledger.parent/"continuations"/continuation_account/"dispatch.sqlite"
                if not continuation_path.exists(): raise ValueError("previous_continuation_ledger_required")
                with closing(sqlite3.connect(continuation_path)) as db, db:
                    continuation_rows=db.execute("SELECT * FROM dispatches ORDER BY operation").fetchall()
                if continuation_rows: raise ValueError("previous_continuation_must_be_unused")
                plan.update(continuation_account_id=continuation_account,continuation_previous_requests=0,
                    continuation_previous_rows_sha256=hashlib.sha256(json.dumps(continuation_rows,sort_keys=True).encode()).hexdigest())
    plan["prepared_id"]=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    save(output/"proposed-live-plan.json",plan)
    save(output/"source.json",source)
    return plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--prepare-live",action="store_true")
    parser.add_argument("--revalidate",type=Path)
    parser.add_argument("--resume-counted-unknown",action="store_true")
    parser.add_argument("--continuation-prefix",default="revision-v3/")
    parser.add_argument("--end-to-end-limit",type=int,default=5)
    parser.add_argument("--supplemental-requests",type=int,choices=[0,24],default=0)
    parser.add_argument("--extra-supplemental-requests",type=int,choices=[0,12],default=0)
    parser.add_argument("--continuation-requests",type=int,choices=[0,6],default=0)
    args=parser.parse_args()
    output=args.output.resolve()
    if output.exists(): raise SystemExit("refusing_to_overwrite_existing_evidence")
    dataset=json.loads(DATA.read_bytes())
    original=args.revalidate.resolve() if args.revalidate else None
    if args.resume_counted_unknown and original is None: raise ValueError("resume_requires_original_plan")
    if original is not None and not args.prepare_live: raise ValueError("revalidation_requires_prepare_live")
    provider=provider_stamp() if args.prepare_live else None
    sys.path.insert(0,str(ROOT))
    with isolated_configuration():
        attempts=[]
        def deny(event,values):
            if event in {"socket.connect","socket.getaddrinfo","socket.sendto"}:
                attempts.append(event)
                raise RuntimeError("offline_semantic_evaluation_network_blocked")
        sys.addaudithook(deny)
        report=offline(dataset)
        report["dataset_sha256"]=hashlib.sha256(DATA.read_bytes()).hexdigest()
        report["blocked_network_operations"]=len(attempts)
        save(output/"report.json",report)
        if args.prepare_live and report["passed"]:
            prepare(output,dataset,provider=provider,revalidate=original,resume_counted_unknown=args.resume_counted_unknown,
                    continuation_prefix=args.continuation_prefix,end_to_end_limit=args.end_to_end_limit,
                    supplemental_requests=args.supplemental_requests,extra_supplemental_requests=args.extra_supplemental_requests,
                    continuation_requests=args.continuation_requests)
        print(json.dumps({"offline_cases":len(dataset["cases"]),"passed":report["passed"],
                          "semantic_model_quality":report["semantic_model_quality"],"provider_requests":0}))
        if not report["passed"]: raise SystemExit(1)


if __name__=="__main__": main()
