from copy import deepcopy

import pytest

from app.agent.answer_contracts import tool_evidence_text
from app.agent.intent_items import snapshot
from app.agent.item_judge_selectors import fixture_selectors,materialize,selector_contract
from scripts.evaluate_item_judge_v2 import synthetic_verdict
from scripts.evaluate_multi_intent import fixture_state,candidate
from scripts.run_item_v2_atomic_acceptance import scope_dataset
from tests.test_item_judge_v2 import assess,configured as configured


def selected(case,bad=False):
    state=fixture_state(case);body,_=candidate(case,bad=bad)
    raw=fixture_selectors(synthetic_verdict(case,bad=bad),body,snapshot(state),tool_evidence_text(state),state.current_evidence())
    return state,body,raw


@pytest.mark.parametrize('case_id',[case['case_id'] for case in scope_dataset()['cases']])
def test_selector_fixtures_keep_semantics_and_exact_server_text(case_id):
    case=next(case for case in scope_dataset()['cases'] if case['case_id']==case_id)
    for bad in (False,True):
        state,body,raw=selected(case,bad)
        result=assess(state,body,raw)
        assert result['publish_allowed'] is not bad,result
        assert all('text' not in slot and 'claims' not in slot for slot in raw['segments'].values())
        assert all('answer_quote' not in check for check in raw['item_checks'])


@pytest.mark.parametrize('fault',['foreign_segment','foreign_source','extra_text','extra_quote','boolean_segment'])
def test_selector_protocol_rejects_forged_choices_instead_of_repairing_them(fault):
    case=scope_dataset()['cases'][0];state,body,raw=selected(case)
    value=deepcopy(raw)
    if fault=='foreign_segment':value['item_checks'][0]['answer_segment_id']=2
    elif fault=='foreign_source':value['segments']['s0']['evidence']=['S3']
    elif fault=='extra_text':value['segments']['s0']['text']='altered text'
    elif fault=='extra_quote':value['item_checks'][0]['answer_quote']='invented quote'
    else:value['item_checks'][0]['answer_segment_id']=False
    with pytest.raises(ValueError):selector_contract(snapshot(state),body).model_validate(value)


def test_mi10_semicolon_is_materialized_from_server_text_without_model_copying():
    case=next(case for case in scope_dataset()['cases'] if case['case_id']=='MI-10')
    state,body,raw=selected(case)
    normalized=materialize(raw,body,snapshot(state),tool_evidence_text(state),state.current_evidence())
    text=normalized['segments']['s1']['claims'][0]['text']
    assert '；本轮未申请退款。' in text and text in body
    assert assess(state,body,raw)['publish_allowed']


def test_normalized_selector_proof_cannot_be_reused_after_source_changes():
    state,body,raw=selected(scope_dataset()['cases'][0])
    proof=assess(state,body,raw);assert proof['publish_allowed']
    state.evidence_bundle.items[0]['content']='changed current source'
    assert not assess(state,body,proof)['publish_allowed']


def test_many_sentences_reuse_one_item_schema_without_weakening_source_ownership():
    import json
    state,body,raw=selected(scope_dataset()['cases'][0])
    long_body=body.replace('退货需保留原包装。','退货需保留原包装。'*15)
    schema=selector_contract(snapshot(state),long_body).model_json_schema()
    fact_types=[key for key in schema['$defs'] if key.startswith('_WholeSentence_')]
    assert len(fact_types)<=len(state.task.items)
    assert len(json.dumps(schema,ensure_ascii=False).encode())<16000
