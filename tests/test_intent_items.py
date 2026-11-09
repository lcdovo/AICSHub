"""逐项执行/审查/发布集成；手工Judge替身仅验证契约，非模型语义质量。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


async def test_two_policy_items_enter_task_even_when_legacy_subrequests_are_empty(monkeypatch):
    """Replay the MI-06 live failure at understanding -> actual task entry."""
    from app.agent.request_understanding import understand,route_understanding
    from app.agent.state import AgentState,AgentNode
    from app.agent.task_loop import enter_task
    from scripts.evaluate_multi_intent import DATA
    from pathlib import Path
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    case=next(row for row in json.loads(DATA.read_bytes())['cases'] if row['case_id']=='MI-06')
    captured=json.loads((Path(__file__).resolve().parents[1]/'acceptance/multi-intent/captured-empty-subrequests-v1.json').read_bytes())
    value=json.loads(captured['response']['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
    from tests.understanding_wire_fixture import item_wire
    value=item_wire(value)  # New provider fixture; original captured receipt stays unchanged.
    assert value['subrequests']==[]
    state=AgentState(session_id='empty-legacy-subrequests',user_id=101,actor_id=101,source_message_id=17,
        user_message=case['message'],understanding_mode='model',task_loop_enabled=True)
    model=SimpleNamespace(run_structured=AsyncMock(return_value=value),is_available=lambda:True)
    parsed=await understand(model,state)
    assert parsed is not None and parsed.task_kind=='multi' and len(parsed.items)==2
    route=route_understanding(state,parsed)
    assert enter_task(state,route)==AgentNode.TASK_EXECUTION
    assert state.task is not None and len(state.task.items)==2 and state.task_runtime is not None


async def test_current_evidence_scope_is_item_bound_and_cannot_complete_a_policy_by_itself(monkeypatch):
    state,_,_=await acquired(monkeypatch)
    context=snapshot(state)
    scope=context['catalog']['S2']['evidence_scope']
    packaging=next(row for row in state.current_evidence() if row['id']=='pack')
    refs=[key for key,entry in context['catalog'].items() if entry.get('source_id')==packaging['id']]
    assert scope['sources']==refs and all(ref in context['items'][1]['evidence_ids'] for ref in refs)
    assert scope['does_not_prove_global_absence'] and scope['does_not_prove_user_eligibility']
    assert 'S2' in context['items'][1]['evidence_ids'] and 'S2' not in context['items'][2]['evidence_ids']
    body,checks=reviews(state)
    checks[1]['evidence_ids']=['S2']
    assert 'i2:scope_manifest_not_business_evidence' in verify_review(state,body,checks)[1]

from app.agent.intent_items import (IntentItem, snapshot,
                                    validate_plan, verify_review, finalize)
from app.agent.request_understanding import ItemizedTaskUnderstanding, route_understanding
from app.agent.state import AgentNode
from app.agent.task_loop import enter_task, allowed_actions, observe_task, TaskExecutionNode, TaskDecisionNode
from app.agent.task_contracts import NextStep
from app.agent.task_completion import completion_gaps
from app.agent.nodes.rag_node import RetrievalNode, RetrievalDependencies
from app.config.config import settings
from tests.test_agent_task_loop import request_state, reader, local_config as local_config


@pytest.fixture(autouse=True)
def item_mode(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_ITEM_COMPLETION_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_JUDGE_CONTRACT_VERSION", "v1")


def current(*, texts=None, kinds=None, order="ORD960001", message=None):
    texts = texts or ["查询订单ORD960001", "退货包装有什么要求", "运费由谁承担"]
    kinds = kinds or ["query_order", "knowledge", "knowledge"]
    message = message or "，".join(texts) + "。"
    state, _ = request_state(kind="multi", message=message, topic="query_order" if order else "refund",
        request_type="query" if order else "inquiry", conditions=[], parameters={},
        entities={"order_id": order, "product": None}, subrequests=[{"topic": "refund", "request_type": "inquiry"}])
    raw = state.understanding.model_dump()
    raw.update(itemization_version="intent-items.v1", items=[{"text": text, "kind": kind} for text, kind in zip(texts, kinds)])
    value = ItemizedTaskUnderstanding.model_validate(raw)
    state.task = state.task_runtime = None
    route = route_understanding(state, value)
    enter_task(state, route)
    return state


def knowledge(content, key):
    rows = [{"id": key, "version_id": "v1", "content": content, "title": "合成政策", "final_score": .9}]
    return {"success": True, "results": rows, "evidence_bundle": {"schema_version": 1,
        "knowledge_epoch": 1, "items": rows}, "meta": {"knowledge_epoch": 1, "score_type": "rrf"}}


def execution(results):
    kb = SimpleNamespace(async_search=AsyncMock(side_effect=results), evidence_is_current=Mock(return_value=True))
    return TaskExecutionNode(RetrievalNode(RetrievalDependencies(lambda: kb, lambda: 3, .35)),
                             SimpleNamespace(prepare=AsyncMock())), kb


async def acquired(monkeypatch, *, second=None):
    state = current()
    reader(monkeypatch)
    node, kb = execution([knowledge("退货需保留包装。", "pack"), second if second is not None else knowledge("质量问题退货运费由商家承担。", "shipping")])
    await node(state)
    await observe_task(state)
    state.task_runtime.next_step = NextStep(action="search_knowledge", knowledge_focus="request")
    await node(state)
    await observe_task(state)
    return state, node, kb


def reviews(state, *, unavailable=False):
    result = []
    quotes = ["订单已签收。", "退货需保留包装。", "当前资料未说明运费承担。" if unavailable else "质量问题退货运费由商家承担。"]
    for item, quote in zip(snapshot(state)["items"], quotes):
        limited = unavailable and item["item_id"] == "i3"
        result.append({"item_id": item["item_id"], "status": "limited" if limited else "answered",
            "answer_quote": quote, "evidence_ids": item["evidence_ids"][:1], "supported": True,
            "conditions_preserved": True, "answerability": "unanswerable" if limited else "answerable"})
    body = "\n".join(f"【{item['item_id']}】{item['answer_quote']}" for item in result)
    return body, result


async def test_each_knowledge_item_runs_its_own_query_and_budget(monkeypatch):
    state, _, kb = await acquired(monkeypatch)
    queries = [call.kwargs["query"] for call in kb.async_search.await_args_list]
    assert queries == ["退货包装有什么要求", "运费由谁承担"]
    assert state.task_runtime.tool_calls == 3 and not completion_gaps(state)
    items = snapshot(state)["items"]
    assert items[1]["evidence_ids"] != items[2]["evidence_ids"]
    assert "finish" in allowed_actions(state) and "search_knowledge" not in allowed_actions(state)


async def test_one_success_does_not_complete_unsearched_sibling(monkeypatch):
    state = current()
    reader(monkeypatch)
    node, _ = execution([])
    await node(state)
    assert "finish" not in allowed_actions(state)
    assert completion_gaps(state) == ["i2:not_attempted", "i3:not_attempted"]


async def test_fully_observed_items_skip_redundant_finish_model_but_require_review(monkeypatch):
    state, _, _ = await acquired(monkeypatch)
    model=SimpleNamespace(run_structured=AsyncMock(side_effect=AssertionError("redundant_finish_model")))
    decision=TaskDecisionNode(lambda:model)
    assert await decision(state)==AgentNode.TASK_EXECUTION
    model.run_structured.assert_not_awaited()
    assert state.task_runtime.next_step.action=="finish"
    assert state.task.status!="completed" and not state.task_runtime.item_reviews


async def test_unfinished_items_still_use_bounded_model_decision(monkeypatch):
    state=current()
    reader(monkeypatch)
    node,_=execution([])
    await node(state)
    await observe_task(state)
    from app.agent.task_contracts import decision_contract
    contract=decision_contract(tuple(allowed_actions(state)))
    model=SimpleNamespace(run_structured=AsyncMock(return_value=contract.model_validate(
        {"step":{"action":"search_knowledge","knowledge_focus":"request"}})))
    assert await TaskDecisionNode(lambda:model)(state)==AgentNode.TASK_EXECUTION
    model.run_structured.assert_awaited_once()
    assert state.task_runtime.next_step.action=="search_knowledge"


@pytest.mark.parametrize("mutation,code", [
    ("omit", "item_review_invalid"), ("duplicate", "item_review_invalid"),
    ("foreign", "i3:foreign_evidence"), ("missing", "i3:answer_missing"),
    ("unsupported", "i3:semantic_rejected"), ("condition", "i3:semantic_rejected"),
    ("limit", "i3:unnecessary_limitation"), ("excerpt", "i3:answer_missing"),
])
async def test_review_rejects_item_omissions_false_sources_and_semantic_failures(monkeypatch, mutation, code):
    state, _, _ = await acquired(monkeypatch)
    body, raw = reviews(state)
    if mutation == "omit": raw.pop()
    elif mutation == "duplicate": raw[-1] = raw[0]
    elif mutation == "foreign": raw[-1]["evidence_ids"] = raw[1]["evidence_ids"]
    elif mutation == "missing": raw[-1]["status"] = "missing"
    elif mutation == "unsupported": raw[-1]["supported"] = False
    elif mutation == "condition": raw[-1]["conditions_preserved"] = False
    elif mutation == "limit": raw[-1]["status"] = "limited"
    elif mutation == "excerpt": raw[-1]["answer_quote"] = "不存在的回答"
    assert code in verify_review(state, body, raw)[1]


async def test_partial_query_preserves_sibling_answers_and_never_marks_completed(monkeypatch):
    state, _, _ = await acquired(monkeypatch, second={"success": True, "results": [], "meta": {}})
    assert "finish" in allowed_actions(state)
    body, raw = reviews(state, unavailable=True)
    parsed, issues = verify_review(state, body, raw)
    assert not issues
    state.task_runtime.item_reviews = parsed
    finalize(state)
    assert state.task.status == "waiting_input" and state.task.item_outcomes == {"i1": "answered", "i2": "answered", "i3": "limited"}


async def test_completed_only_after_all_independent_item_reviews_pass(monkeypatch):
    state, _, _ = await acquired(monkeypatch)
    body, raw = reviews(state)
    state.task_runtime.item_reviews, issues = verify_review(state, body, raw)
    assert not issues
    finalize(state)
    assert state.task.status == "completed" and set(state.task.item_outcomes.values()) == {"answered"}


async def test_new_round_does_not_reuse_item_evidence_or_reviews(monkeypatch):
    state, _, _ = await acquired(monkeypatch)
    state.task = state.task.model_copy(update={"status": "waiting_input"})
    fresh = state.for_next_turn(101, "继续解释运费")
    assert fresh.task.items == state.task.items
    assert fresh.task_runtime is None and fresh.current_evidence() == [] and fresh.tool_calls == []


async def test_evidence_mutation_invalidates_the_matching_item(monkeypatch):
    state, _, _ = await acquired(monkeypatch)
    state.evidence_bundle.items[-1]["content"] = "被替换的正文"
    assert "i3:stale_evidence" in completion_gaps(state)
    assert "finish" not in allowed_actions(state)


def test_duplicate_and_invented_item_sources_are_rejected():
    state = current()
    value = state.understanding.model_copy(update={"items": [IntentItem(text="虚构诉求", kind="knowledge"), IntentItem(text="运费由谁承担", kind="knowledge")]})
    with pytest.raises(ValueError, match="current_message"):
        validate_plan(state, value)
    value = value.model_copy(update={"items": [IntentItem(text="运费由谁承担", kind="knowledge")] * 2})
    with pytest.raises(ValueError, match="duplicate"):
        validate_plan(state, value)


async def test_missing_order_does_not_discard_answerable_policy():
    state = current(texts=["查我的订单状态", "退货包装有什么要求"], kinds=["query_order", "knowledge"], order=None)
    node, _ = execution([knowledge("退货需保留包装。", "pack")])
    await node(state)
    assert snapshot(state)["items"][0]["error_code"] == "missing_order_id"
    assert not completion_gaps(state) and "finish" in allowed_actions(state)


async def test_structured_review_is_required_in_response_pipeline(monkeypatch):
    from app.agent.answer_review import validate_candidate
    from app.agent.validation import ValidationResult
    state, _, kb = await acquired(monkeypatch)
    body, raw = reviews(state)
    state.reply = body
    base = {"grounded": True, "fact_score": 1., "has_unsupported_claim": False, "refuse_recommended": False}
    context = SimpleNamespace(knowledge_factory=lambda: kb,
        validator=SimpleNamespace(validate=lambda **kwargs: ValidationResult()),
        llm_judge=SimpleNamespace(judge=AsyncMock(return_value=base)))
    assert not await validate_candidate(context, state)
    assert state.review_result.status == "unknown"
    context.llm_judge.judge.return_value = {**base, "item_checks": raw}
    assert await validate_candidate(context, state)
    assert len(state.task_runtime.item_reviews) == 3


async def test_accepted_items_publish_and_persist_status_after_final_guard(monkeypatch):
    from app.agent.answer_review import validate_candidate
    from app.agent.answer_publication import publish_answer
    from app.agent.answer_contracts import AnswerDraft
    from app.agent.validation import ValidationResult
    from app.agent.nodes.response_node import add_rag_citations
    state, _, kb = await acquired(monkeypatch)
    body, raw = reviews(state)
    state.reply = body
    context = SimpleNamespace(knowledge_factory=lambda: kb,
        validator=SimpleNamespace(validate=lambda **kwargs: ValidationResult()),
        llm_judge=SimpleNamespace(judge=AsyncMock(return_value={"grounded":True,"fact_score":1.,
            "has_unsupported_claim":False,"refuse_recommended":False,"item_checks":raw})))
    context._clean_chunk=lambda content,title:(title,"",content)
    context._add_rag_citations=lambda body,rows,**kwargs:add_rag_citations(context,body,rows,**kwargs)
    assert await validate_candidate(context,state)
    assert await publish_answer(context,state,AnswerDraft(generated=True)) == AgentNode.END
    assert state.task.status == "completed" and state.task.stop_reason == "all_items_answered"
    assert state.reply.startswith("【i1】") and len(state.task.item_outcomes)==3


async def test_review_waiting_time_evidence_mutation_prevents_item_completion(monkeypatch):
    from app.agent.answer_review import validate_candidate
    from app.agent.validation import ValidationResult
    state, _, kb = await acquired(monkeypatch)
    body, raw = reviews(state)
    state.reply = body
    async def changing(**kwargs):
        state.evidence_bundle.items[-1]["content"]="changed after review started"
        return {"grounded":True,"fact_score":1.,"has_unsupported_claim":False,"refuse_recommended":False,"item_checks":raw}
    context=SimpleNamespace(knowledge_factory=lambda:kb,validator=SimpleNamespace(validate=lambda **kwargs:ValidationResult()),
        llm_judge=SimpleNamespace(judge=changing))
    assert not await validate_candidate(context,state)
    assert state.review_result.status=="unknown" and "review_input_changed" in state.review_result.issue_codes


def test_item_resume_preserves_original_source_and_rejects_omission():
    state=current()
    state.task=state.task.model_copy(update={"status":"waiting_input"})
    original=list(state.task.items)
    fresh=state.for_next_turn(101,"继续处理")
    fresh.actor_id=101; fresh.source_message_id=99
    value=state.understanding.model_copy(update={"continue_task":True})
    assert validate_plan(fresh,value)==original
    with pytest.raises(ValueError,match="continued_items_changed"):
        validate_plan(fresh,value.model_copy(update={"items":value.items[:-1]}))


async def test_retrieval_epoch_change_stops_instead_of_mixing_sources(monkeypatch):
    state=current()
    reader(monkeypatch)
    first=knowledge("退货需保留包装。","pack")
    second=knowledge("质量问题运费由商家承担。","shipping")
    second["meta"]["knowledge_epoch"]=2; second["evidence_bundle"]["knowledge_epoch"]=2
    node,_=execution([first,second])
    await node(state)
    state.task_runtime.next_step=NextStep(action="search_knowledge",knowledge_focus="request")
    assert await node(state)==AgentNode.RESPONSE_GENERATION
    assert not state.current_evidence() and state.task.status=="stopped"


async def test_item_search_budget_is_reserved_before_any_partial_dispatch(monkeypatch):
    state=current(texts=["查订单ORD960001","包装要求","运费承担","到账时间"],
                  kinds=["query_order","knowledge","knowledge","knowledge"])
    reader(monkeypatch)
    node,kb=execution([])
    await node(state)
    monkeypatch.setattr(settings,"AGENT_TASK_MAX_TOOL_CALLS",3)
    state.task_runtime.next_step=NextStep(action="search_knowledge",knowledge_focus="request")
    await node(state)
    kb.async_search.assert_not_awaited()
    assert state.task.status=="stopped"


async def test_item_diagnostic_steps_cannot_claim_resolved_without_current_user_feedback(monkeypatch):
    state=current(texts=["查订单ORD960001","耳机无法开机请排查"],kinds=["query_order","troubleshoot"])
    reader(monkeypatch)
    node,_=execution([knowledge("先换充电线并反馈灯的状态。","diagnostic")])
    await node(state)
    state.task_runtime.next_step=NextStep(action="search_knowledge",knowledge_focus="request")
    await node(state)
    items=snapshot(state)["items"]
    refs=items[1]["evidence_ids"]
    raw=[{"item_id":"i1","status":"answered","answer_quote":"已签收。","evidence_ids":items[0]["evidence_ids"],
          "supported":True,"conditions_preserved":True,"answerability":"answerable"},
         {"item_id":"i2","status":"answered","answer_quote":"问题已解决。","evidence_ids":refs,
          "supported":True,"conditions_preserved":True,"answerability":"answerable"}]
    assert "i2:diagnosis_not_resolved" in verify_review(state,"【i1】已签收。\n【i2】问题已解决。",raw)[1]
    raw[1].update(status="waiting_feedback",answer_quote="先换充电线并反馈。",answerability="partial")
    state.task_runtime.item_reviews,issues=verify_review(state,"【i1】已签收。\n【i2】先换充电线并反馈。",raw)
    assert not issues
    finalize(state)
    assert state.task.status=="waiting_input" and state.task.waiting_for=="diagnostic_feedback"


def test_old_task_json_loads_without_item_fields():
    from app.agent.task_contracts import TaskProgress
    old=current().task.model_dump(exclude={"items","item_outcomes"})
    assert TaskProgress.model_validate(old).items==[]


def test_item_feature_requires_independent_review():
    from app.config.config import Settings
    with pytest.raises(ValueError,match="逐项完成检查"):
        Settings(_env_file=None,AGENT_ITEM_COMPLETION_ENABLED=True,AGENT_TASK_LOOP_ENABLED=False)


async def test_item_refund_preview_requires_all_read_items_and_does_not_submit(monkeypatch):
    from scripts.evaluate_multi_intent import fixture_state,DATA,candidate
    import json
    case=next(case for case in json.loads(DATA.read_bytes())["cases"] if case["case_id"]=="MI-07")
    state=fixture_state(case)
    body,raw=candidate(case)
    state.task_runtime.item_reviews,issues=verify_review(state,body,raw)
    assert not issues
    finalize(state)
    assert state.task.status=="waiting_confirmation" and state.task.item_outcomes["i2"]=="awaiting_confirmation"
    assert not state.has_current_refund_confirmation() and state.current_refund_action().status=="pending"


async def test_review_quote_may_include_its_own_section_marker_but_not_other_sections(monkeypatch):
    state,_,_=await acquired(monkeypatch)
    body,raw=reviews(state)
    for entry in raw:
        entry["answer_quote"]="【"+entry["item_id"]+"】"+entry["answer_quote"]
    assert not verify_review(state,body,raw)[1]
    raw[-1]["answer_quote"]=raw[-2]["answer_quote"]
    assert "i3:answer_missing" in verify_review(state,body,raw)[1]


def test_captured_provider_marker_excerpt_is_valid_but_changed_amount_is_not():
    import json
    from pathlib import Path
    from scripts.evaluate_multi_intent import fixture_state,candidate
    path=Path(__file__).resolve().parents[1]/"acceptance/multi-intent/captured-review-v1.json"
    value=json.loads(path.read_bytes())
    state=fixture_state(value["scenario"])
    body,_=candidate(value["scenario"])
    _,issues=verify_review(state,body,value["good_item_checks"])
    assert not issues
    bad,_=candidate(value["scenario"],bad=True)
    assert "i2:semantic_rejected" in verify_review(state,bad,value["bad_item_checks"])[1]


def test_captured_conditional_two_goals_cannot_silently_drop_itemization():
    import json
    from pathlib import Path
    from scripts.evaluate_multi_intent import fixture_state
    from app.agent.request_understanding import validate_semantics
    value=json.loads((Path(__file__).resolve().parents[1]/
        "acceptance/multi-intent/captured-conditional-v1.json").read_bytes())
    state=fixture_state(value["scenario"])
    with pytest.raises(ValueError,match="items"):
        ItemizedTaskUnderstanding.model_validate(value["understanding"])
    # An internal prerequisite read alone does not add a user-facing goal.
    raw=dict(value["understanding"])
    raw['subrequests']=[{'topic':'refund','request_type':'apply'}]
    from app.agent.state import AgentState
    message="如果订单ORD960001已签收请申请退款"
    single_state=AgentState(session_id="single-conditional",user_id=101,actor_id=101,
                           user_message=message,understanding_mode="model",task_loop_enabled=True)
    raw['items']=[{'text':message,'kind':'prepare_refund'}]
    single=ItemizedTaskUnderstanding.model_validate(raw)
    assert validate_semantics(single,single_state) is single


def test_item_count_owns_task_kind_instead_of_provider_classification():
    state=current(texts=['查订单ORD960001状态','再查这笔订单金额'],kinds=['query_order','query_order'])
    raw=state.understanding.model_dump()
    raw['task_kind']='single'
    value=ItemizedTaskUnderstanding.model_validate(raw)
    assert value.task_kind=='multi' and len(value.items)==2
    assert 'task_kind' not in ItemizedTaskUnderstanding.model_json_schema()['properties']
    assert ItemizedTaskUnderstanding.model_validate(value.model_dump()).task_kind=='multi'
    raw['items']=[]
    with pytest.raises(ValueError,match='items'): ItemizedTaskUnderstanding.model_validate(raw)


def test_single_policy_item_keeps_single_request_path():
    state=current(texts=['退货包装有什么要求'],kinds=['knowledge'],order=None)
    assert state.understanding.task_kind=='single' and not state.task and not state.task_runtime


def test_captured_conditional_explanation_cannot_mark_refund_completed():
    import json
    from copy import deepcopy
    from pathlib import Path
    from scripts.evaluate_multi_intent import fixture_state,candidate
    value=json.loads((Path(__file__).resolve().parents[1]/
        "acceptance/multi-intent/captured-conditional-v1.json").read_bytes())
    state=fixture_state(value["scenario"]);body,_=candidate(value["scenario"])
    assert "i2:unproven_completion" in verify_review(state,body,value["good_verdict"]["item_checks"])[1]
    corrected=deepcopy(value["good_verdict"]["item_checks"])
    corrected[1].update(status="limited",answerability="unanswerable")
    state.task_runtime.item_reviews,issues=verify_review(state,body,corrected)
    assert not issues
    finalize(state)
    assert state.task.status=="waiting_input" and state.task.item_outcomes["i2"]=="limited"
    bad,_=candidate(value["scenario"],bad=True)
    assert verify_review(state,bad,value["bad_verdict"]["item_checks"])[1]
    assert not state.current_refund_preview() and not state.has_current_refund_confirmation()


def test_judge_schema_restricts_each_item_to_current_legal_stages():
    import json
    from copy import deepcopy
    from scripts.evaluate_multi_intent import DATA,fixture_state,candidate
    from app.agent.validation_llm import item_judge_contract
    for case in json.loads(DATA.read_bytes())["cases"]:
        state=fixture_state(case);_,checks=candidate(case)
        contract=item_judge_contract(snapshot(state))
        payload={'grounded':True,'fact_score':.95,'has_unsupported_claim':False,
                 'refuse_recommended':False,'reason':'synthetic-contract','item_checks':checks}
        assert len(contract.model_validate(payload).item_checks)==len(case['items'])
        if case['case_id'] in {'MI-04','MI-07','MI-08','MI-09','MI-13'}:
            wrong=deepcopy(payload)
            index=0 if case['case_id']=='MI-04' else 1
            wrong['item_checks'][index]['status']='waiting_feedback' if case['case_id']=='MI-04' else 'submitted'
            with pytest.raises(ValueError): contract.model_validate(wrong)


def test_captured_missing_order_cannot_authorize_history_or_use_diagnostic_status():
    import json
    from pathlib import Path
    from app.agent.request_understanding import validate_semantics
    from app.agent.validation_llm import item_judge_contract
    from scripts.evaluate_multi_intent import fixture_state
    value=json.loads((Path(__file__).resolve().parents[1]/
        'acceptance/multi-intent/captured-missing-order-v1.json').read_bytes())
    state=fixture_state(value['scenario'])
    with pytest.raises(ValueError):
        validate_semantics(ItemizedTaskUnderstanding.model_validate(value['understanding']),state)
    with pytest.raises(ValueError):
        item_judge_contract(snapshot(state)).model_validate(value['good_verdict'])
    assert not state.current_refund_preview() and not state.tool_calls


def test_captured_preview_answerability_is_separate_from_action_completion():
    import json
    from pathlib import Path
    from app.agent.validation_llm import item_judge_contract
    from scripts.evaluate_multi_intent import fixture_state,candidate
    value=json.loads((Path(__file__).resolve().parents[1]/
        'acceptance/multi-intent/captured-preview-v1.json').read_bytes())
    state=fixture_state(value['scenario']);contract=item_judge_contract(snapshot(state))
    good=contract.model_validate(value['good_verdict'])
    body,_=candidate(value['scenario'])
    state.task_runtime.item_reviews,issues=verify_review(state,body,[item.model_dump() for item in good.item_checks])
    assert not issues
    finalize(state)
    assert state.task.status=='waiting_confirmation'
    assert state.task.item_outcomes['i2']=='awaiting_confirmation'
    bad=contract.model_validate(value['bad_verdict'])
    body,_=candidate(value['scenario'],bad=True)
    assert verify_review(state,body,[item.model_dump() for item in bad.item_checks])[1]


async def test_failed_condition_is_an_explicit_unfulfilled_item_not_success():
    from scripts.evaluate_multi_intent import fixture_state,DATA,candidate
    import json
    case=next(case for case in json.loads(DATA.read_bytes())["cases"] if case["case_id"]=="MI-08")
    state=fixture_state(case)
    assert "prepare_refund" not in allowed_actions(state)
    assert state.task_runtime.item_observations["i2"].error_code=="condition_not_satisfied"
    body,raw=candidate(case)
    state.task_runtime.item_reviews,issues=verify_review(state,body,raw)
    assert not issues
    finalize(state)
    assert state.task.status=="waiting_input" and state.task.item_outcomes["i2"]=="limited"


async def test_task_api_returns_each_owned_item_and_excludes_other_user(tmp_path,monkeypatch):
    from tests.test_ticket_idempotency import api_context
    from app.services.agent_tasks import persist_task
    from app.routers.chat import router
    state,_,_=await acquired(monkeypatch)
    async with api_context(tmp_path/"items.db") as (client,factory):
        client._transport.app.include_router(router)
        async with factory() as db:
            await persist_task(db,state)
            await db.commit()
        response=await client.get("/api/v1/chat/tasks/"+state.task.task_id)
        assert response.status_code==200
        rows=response.json()["data"]["items"]
        assert len(rows)==3 and rows[0]["source_message_id"]==17
        assert rows[0]["status"]=="pending"
        response=await client.get("/api/v1/chat/tasks/"+state.task.task_id,headers={"X-Test-Actor":"202"})
        assert response.status_code==403
