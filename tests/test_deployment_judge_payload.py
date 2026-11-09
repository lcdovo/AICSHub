"""Actual published policy shape must fit the unchanged dispatch byte ceiling."""
import json
from pathlib import Path
from types import SimpleNamespace

from app.agent.answer_contracts import review_query,tool_evidence_text
from app.agent.intent_items import observe,evidence_digest,snapshot,REVIEW_CONTEXT
from app.agent.item_evidence_reply import evidence_reply
from app.agent.item_judge_selectors import selector_contract
from app.agent.judge_contracts import response_segments
from app.agent.validation_llm import LLMRAGJudge
from scripts.evaluate_multi_intent import DATA,fixture_state
from scripts.m5_live_budget import DispatchLedger
from tests.test_item_judge_v2 import configured as configured


def published_state():
    case=next(c for c in json.loads(DATA.read_bytes())['cases'] if c['case_id']=='MI-01')
    case={**case,'message':'查询订单ORD960001，退货需要保留原包装吗。','items':case['items'][:2],'knowledge':case['knowledge'][:2]}
    case['items'][1]={'kind':'knowledge','text':'退货需要保留原包装吗'}
    state=fixture_state(case)
    rows=json.loads((Path(__file__).resolve().parents[1]/'acceptance/multi-intent/captured-published-packaging-v1.json').read_bytes())['items']
    state.accept_retrieval({'success':True,'results':rows,'meta':{'score_type':'rrf','knowledge_epoch':1},
        'evidence_bundle':{'schema_version':1,'items':rows,'knowledge_epoch':1}})
    observe(state,'i2',[evidence_digest(row) for row in rows])
    return state


async def test_deployed_policy_shape_enters_guarded_transport_without_larger_budget(tmp_path):
    state=published_state();body=evidence_reply(state)
    assert body is not None and len(response_segments(body))>=20
    calls=[]
    ledger=DispatchLedger(tmp_path/'dispatch.sqlite','offline-only',
        {'max_requests':1,'max_input_tokens':24576,'max_output_tokens':2048},{'judge':2048})
    async def structured(system,prompt,contract,**kwargs):
        # The same JSON-object schema inclusion as run_structured. Conservative
        # HTTP-shaped reserve includes model/options overhead and all UTF-8 text.
        system+='\n完整JSON Schema：'+json.dumps(contract.model_json_schema(),ensure_ascii=False,separators=(',',':'))
        payload={'model':'synthetic','messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
            'max_tokens':2048,'response_format':{'type':'json_object'},'stream':False}
        wire=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        ledger.reserve('judge',wire);calls.append(len(wire))
        return None
    token=REVIEW_CONTEXT.set(snapshot(state))
    try:
        await LLMRAGJudge(SimpleNamespace(is_available=lambda:True,run_structured=structured)).judge(
            body,review_query(state),state.current_evidence(),tool_evidence_text(state))
    finally:REVIEW_CONTEXT.reset(token)
    assert len(calls)==1 and calls[0]<24576
    assert ledger.snapshot()['requests']==1


def test_shared_schema_keeps_independent_sentence_verdicts():
    state=published_state();body=evidence_reply(state);context=snapshot(state)
    schema=selector_contract(context,body).model_json_schema()
    assert len(schema['properties']['segments']['$ref'])>0
    assert len([key for key in schema['$defs'] if key.startswith('_WholeSentence_')])==2
    # Reusing definitions never merges segment identities or their item owners.
    slots=schema['$defs']['_SelectorSlots']['properties']
    assert len(slots)==len(response_segments(body))


def test_published_policy_returned_verdict_can_cover_every_existing_sentence():
    from app.agent.item_judge_selectors import layout
    from tests.test_item_judge_v2 import assess
    state=published_state();body=evidence_reply(state);context=snapshot(state)
    segments,owners=layout(context,body)
    value={'schema_version':2,'answerability':'full','response_behavior':'answer','response_validity':'valid',
        'claims_complete':True,'response_issues':[],'source_contract_version':'server-selectors.v1',
        'segments':{},'item_checks':[]}
    for segment in segments:
        owner=owners[segment['segment_id']]
        item=next(i for i in context['items'] if i['item_id']==owner)
        value['segments'][f's{segment["segment_id"]}']={'kind':'facts','support':'supported',
            'evidence':[r for r in item['evidence_ids'] if r.startswith(('T','K'))],'issues':[]}
    for item in context['items']:
        value['item_checks'].append({'item_id':item['item_id'],'status':'answered','supported':True,
            'conditions_preserved':True,'answerability':'answerable',
            'answer_segment_id':next(index for index,owner in owners.items() if owner==item['item_id'])})
    # Synthetic semantic labels only test lossless transport/decoding, not model quality.
    assert len(segments)>20
    result=assess(state,body,value)
    assert result['publish_allowed'],result
