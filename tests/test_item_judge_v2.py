"""v2 item bridge contract tests; synthetic verdicts are not semantic model acceptance."""
from copy import deepcopy
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config.config import settings
from app.agent.intent_items import REVIEW_CONTEXT, snapshot, verify_review, finalize
from app.agent.item_judge_v2 import contract, review_sources
from app.agent.judge_contracts import evidence_catalog, response_segments
from app.agent.validation_llm import LLMRAGJudge, assess_judgement
from app.agent.answer_contracts import review_query, tool_evidence_text
from scripts.evaluate_multi_intent import DATA, fixture_state, candidate


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for key,value in {'REQUEST_UNDERSTANDING_MODE':'model','AGENT_TASK_LOOP_ENABLED':True,
        'AGENT_ITEM_COMPLETION_ENABLED':True,'LLM_JUDGE_ENABLED':True,'LLM_JUDGE_CONTRACT_VERSION':'v2',
        'USER_MEMORY_ENABLED':False}.items(): monkeypatch.setattr(settings,key,value)


def fixture(case_id):
    case=next(case for case in json.loads(DATA.read_bytes())['cases'] if case['case_id']==case_id)
    state=fixture_state(case)
    body,checks=candidate(case)
    context=snapshot(state)
    tools=tool_evidence_text(state)
    augmented,identities=review_sources(tools,context,state.current_evidence())
    catalog=evidence_catalog(state.current_evidence(),augmented)
    claims=[];nonfact=[]
    sections=list(re.finditer(r'【(i[1-4])】(.*?)(?=【i[1-4]】|\Z)',body,re.S))
    for segment in response_segments(body):
        start=body.find(segment['text'])
        owner=next(section.group(1) for section in sections if section.start()<=start<section.end())
        check=next(check for check in checks if check['item_id']==owner)
        if check['evidence_ids']:
            evidence=[]
            for short in check['evidence_ids']:
                kind,source,version=identities[short]
                evidence.append({'kind':kind,'source_id':source,'version_id':version,'quote':catalog[(kind,source,version)]})
            claims.append({'segment_id':segment['segment_id'],'text':segment['text'],'support':'supported',
                'evidence':evidence,'issues':[]})
        else:
            nonfact.append({'segment_id':segment['segment_id'],'kind':'insufficient_notice'})
    full=all(check['status']=='answered' for check in checks)
    value={'schema_version':2,'answerability':'full' if full else 'partial',
        'response_behavior':'answer' if full else 'answer_with_limits','response_validity':'valid',
        'claims_complete':True,'claims':claims,'nonfact_segments':nonfact,'response_issues':[],
        'item_checks':checks}
    return state,body,value


def assess(state,body,value):
    return assess_judgement(value,response=body,kb_context=state.current_evidence(),
        tool_evidence=tool_evidence_text(state),user_query=review_query(state),
        contract_version='v2',item_context=snapshot(state))


async def test_captured_answered_partial_contradiction_still_blocks_completion():
    # The MI-16 capture was correctly stopped. A prompt change must not relax
    # this consistency check to make a live evaluation pass.
    state,body,value=fixture('MI-16')
    value['item_checks'][2]['answerability']='partial'
    from app.agent.answer_review import validate_candidate
    from app.agent.validation import ValidationResult
    from app.agent.item_judge_v2 import to_fragment_verdict,to_sentence_verdict
    state.reply=body
    value=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    from app.agent.item_judge_selectors import fixture_selectors
    value=fixture_selectors(value,body,snapshot(state),tool_evidence_text(state),state.current_evidence())
    model=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=value))
    context=SimpleNamespace(knowledge_factory=lambda:SimpleNamespace(evidence_is_current=lambda items:True),
        validator=SimpleNamespace(validate=lambda **kwargs:ValidationResult()),llm_judge=LLMRAGJudge(model))
    assert not await validate_candidate(context,state)
    assert not state.review_result.passed


def test_captured_advice_regressions_need_semantic_judgement_not_just_valid_json():
    from pathlib import Path
    from scripts.evaluate_multi_intent import offline
    path=Path(__file__).resolve().parents[1]/'acceptance/multi-intent/v2-generation-regressions-v1.json'
    result=offline(json.loads(path.read_bytes()))
    assert result['passed'] and len(result['cases'])==2
    assert result['semantic_model_quality']=='NOT_RUN'
    # Explicitly expose the limitation: syntactically valid false semantic
    # labels can pass. This is never reported as live model regression success.
    assert result['semantic_dependence_demonstrated']==2


def test_scope_manifest_does_not_repair_an_empty_fact_reference():
    state,body,value=fixture('MI-16')
    assert 'S3' in snapshot(state)['catalog']
    value['claims'][2]['evidence']=[]
    assert not assess(state,body,value)['publish_allowed']


def test_scope_manifest_is_invalidated_by_current_source_content():
    from app.agent.item_judge_v2 import fragments
    state,_,_=fixture('MI-16')
    old=next(row for row in fragments(tool_evidence_text(state),snapshot(state),state.current_evidence()) if row['short_id']=='S3')
    entry=next(row for row in state.evidence_bundle.items if row['id'].endswith('-i3'))
    entry['content']='新版本规则：运费由用户承担。'
    new=fragments(tool_evidence_text(state),snapshot(state),state.current_evidence())
    assert old['fragment_id'] not in {row['fragment_id'] for row in new}


def test_scope_manifest_is_not_available_for_an_old_turn_observation():
    state,_,_=fixture('MI-16')
    state.task_runtime.item_observations['i3']=state.task_runtime.item_observations['i3'].model_copy(update={'scope':'old-turn'})
    assert 'S3' not in snapshot(state)['catalog']
    assert not snapshot(state)['items'][2]['evidence_ids']


def test_scope_manifest_cannot_cross_item_ownership():
    state,body,value=fixture('MI-16')
    tools,identities=review_sources(tool_evidence_text(state),snapshot(state),state.current_evidence())
    sources=evidence_catalog(state.current_evidence(),tools)
    kind,source,version=identities['S2']
    value['claims'][2]['evidence']=[{'kind':kind,'source_id':source,'version_id':version,'quote':sources[(kind,source,version)]}]
    assert not assess(state,body,value)['publish_allowed']


def test_wire_projection_keeps_complete_content_and_immutable_fragment_ids():
    from app.agent.item_judge_v2 import fragments,wire_fragments
    state,_,_=fixture('MI-16')
    args=(tool_evidence_text(state),snapshot(state),state.current_evidence())
    full=fragments(*args);wire=wire_fragments(*args)
    assert len(full)==len(wire)
    for source,sent in zip(full,wire):
        assert sent=={key:source[key] for key in ('fragment_id','short_id','kind','content','allowed_item_ids')}
        assert 'source_id' in source and 'version_id' in source


@pytest.mark.parametrize('case_id',[f'MI-{index:02d}' for index in range(1,17)])
def test_all_synthetic_item_contracts_bind_claims_and_item_stage(case_id):
    state,body,value=fixture(case_id)
    contract(snapshot(state)).model_validate(value)
    result=assess(state,body,value)
    assert result['publish_allowed'],result
    reviews,issues=verify_review(state,body,result['item_checks'])
    assert not issues
    state.task_runtime.item_reviews=reviews
    finalize(state)
    assert state.task.status==('completed' if all(check['status']=='answered' for check in value['item_checks']) else
        'waiting_confirmation' if case_id=='MI-07' else 'waiting_input')


@pytest.mark.parametrize('fault',['foreign','quote','version','omitted_claim','nonfact_completion','omitted_item','context','preview_as_submitted'])
def test_claims_and_items_cannot_bypass_each_other(fault):
    state,body,value=fixture('MI-07' if fault=='preview_as_submitted' else 'MI-01')
    if fault=='foreign': value['claims'][1]['evidence']=deepcopy(value['claims'][2]['evidence'])
    elif fault=='quote': value['claims'][1]['evidence'][0]['quote']='不存在的来源原文'
    elif fault=='version': value['claims'][1]['evidence'][0]['version_id']='other-version'
    elif fault=='omitted_claim': value['claims'].pop()
    elif fault=='nonfact_completion':
        claim=value['claims'].pop()
        value['nonfact_segments'].append({'segment_id':claim['segment_id'],'kind':'social'})
    elif fault=='omitted_item': value['item_checks'].pop()
    elif fault=='preview_as_submitted': value['item_checks'][1]['status']='answered'
    elif fault=='context':
        value=assess(state,body,value)
        state.task_runtime.item_observations.pop('i3')
    result=assess(state,body,value)
    assert not result['publish_allowed'] and result['support']=='unknown'


@pytest.mark.parametrize('mutation',['response','knowledge','tool','query'])
def test_normalized_item_proof_rebinds_all_current_inputs(mutation):
    state,body,value=fixture('MI-01')
    proof=assess(state,body,value);assert proof['publish_allowed']
    if mutation=='response': body+='额外结论。'
    elif mutation=='knowledge': state.evidence_bundle.items[0]['version_id']='changed'
    elif mutation=='tool': state.tool_calls[0].result['data']['total_amount']='999.10'
    elif mutation=='query': state.user_message='不同问题'
    assert not assess(state,body,proof)['publish_allowed']


async def test_response_pipeline_keeps_v2_claim_proof_and_item_completion():
    from app.agent.answer_review import validate_candidate
    from app.agent.answer_publication import publish_answer
    from app.agent.answer_contracts import AnswerDraft
    from app.agent.validation import ValidationResult
    state,body,value=fixture('MI-01');state.reply=body
    from app.agent.item_judge_v2 import to_fragment_verdict,to_sentence_verdict
    value=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    from app.agent.item_judge_selectors import fixture_selectors
    value=fixture_selectors(value,body,snapshot(state),tool_evidence_text(state),state.current_evidence())
    model=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=value))
    context=SimpleNamespace(knowledge_factory=lambda:SimpleNamespace(evidence_is_current=lambda items:True),
        validator=SimpleNamespace(validate=lambda **kwargs:ValidationResult()),llm_judge=LLMRAGJudge(model),
        _add_rag_citations=lambda text,items,**kwargs:text)
    assert await validate_candidate(context,state)
    assert len(state.task_runtime.item_reviews)==3
    assert '_v2_verdict' not in state.collected_info['llm_judge']
    await publish_answer(context,state,AnswerDraft())
    assert state.review_result.passed and state.task.status=='completed'
    assert all(status=='answered' for status in state.task.item_outcomes.values())
    model.run_structured.assert_awaited_once()


async def test_judge_uses_one_combined_v2_contract_and_synthetic_preview_source():
    state,body,value=fixture('MI-07')
    from app.agent.item_judge_v2 import to_fragment_verdict,to_sentence_verdict
    value=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    from app.agent.item_judge_selectors import fixture_selectors
    value=fixture_selectors(value,body,snapshot(state),tool_evidence_text(state),state.current_evidence())
    model=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=value))
    token=REVIEW_CONTEXT.set(snapshot(state))
    try: result=await LLMRAGJudge(model).judge(body,review_query(state),state.current_evidence(),tool_evidence_text(state))
    finally: REVIEW_CONTEXT.reset(token)
    assert result['publish_allowed']
    arguments=model.run_structured.call_args.args
    assert 'current_refund_preview' in arguments[1] and 'application_submitted' in arguments[1]
    assert arguments[2].__name__=='ItemJudgeVerdictV2'
    assert result['item_checks'][1]['status']=='awaiting_confirmation'
    assert not state.has_current_refund_confirmation()


def test_v2_item_config_still_requires_task_loop_gateway_and_judge():
    from app.config.config import Settings
    base=dict(_env_file=None,AGENT_ITEM_COMPLETION_ENABLED=True,AGENT_TASK_LOOP_ENABLED=True,
        REQUEST_UNDERSTANDING_MODE='model',LLM_GATEWAY_ENABLED=True,LLM_JUDGE_CONTRACT_VERSION='v2')
    assert Settings(**base).LLM_JUDGE_CONTRACT_VERSION=='v2'
    with pytest.raises(ValueError): Settings(**base,LLM_JUDGE_ENABLED=False)
    base['LLM_GATEWAY_ENABLED']=False
    with pytest.raises(ValueError): Settings(**base)


def test_normalized_item_metadata_cannot_override_the_raw_v2_proof():
    state,body,value=fixture('MI-01')
    proof=assess(state,body,value);assert proof['publish_allowed']
    proof['item_checks'][0]['status']='limited'
    assert not assess(state,body,proof)['publish_allowed']


def test_fixed_v2_diagnostic_fits_existing_eighteen_request_accounts():
    from scripts.evaluate_item_judge_v2 import operations,CASES,synthetic_verdict
    assert len(CASES)==6 and len(operations())==18
    assert all(value==1024 for value in operations().values())
    dataset=json.loads(DATA.read_bytes())
    for case in dataset['cases']:
        if case['case_id'] not in CASES: continue
        state=fixture_state(case)
        for bad in (False,True):
            body,_=candidate(case,bad=bad)
            result=assess(state,body,synthetic_verdict(case,bad=bad))
            _,issues=verify_review(state,body,result.get('item_checks'))
            assert bool(result['publish_allowed'] and not issues) is (not bad)


def test_sixteen_request_verification_preserves_previous_operations():
    from scripts.evaluate_item_judge_v2 import operations
    previous=['item-v2/MI-15/good','item-v2/MI-15/bad']
    current=operations('item-v2-verify/',4,previous)
    assert len(current)==18 and sum(key.startswith('item-v2-verify/') for key in current)==16
    assert all(key in current for key in previous)
    assert 'item-v2-verify/E2E-MI-01/http-5' not in current


def test_fragment_diagnostic_cannot_exceed_remaining_ten_requests():
    from scripts.evaluate_item_judge_v2 import operations,FRAGMENT_CASES
    current=operations('item-v2-fragments/',4,(),FRAGMENT_CASES)
    assert len(current)==10
    assert 'item-v2-fragments/MI-07/good' in current
    assert 'item-v2-fragments/MI-15/good' not in current


def test_derived_source_full_graph_has_only_four_allowed_operations():
    from scripts.evaluate_item_judge_v2 import operations
    assert len(operations('item-v2-derived/',4,(),()))==4
    assert 'item-v2-derived/MI-07/good' not in operations('item-v2-derived/',4,(),())


async def test_captured_invalid_json_is_not_repaired_or_allowed():
    from pathlib import Path
    capture=json.loads((Path(__file__).resolve().parents[1]/
        'acceptance/multi-intent/captured-item-v2-invalid-json-v1.json').read_bytes())
    assert capture['finish_reason']=='tool_calls'
    with pytest.raises(json.JSONDecodeError): json.loads(capture['arguments'])
    state,body,_=fixture('MI-15')
    async def parse(*args,**kwargs): return json.loads(capture['arguments'])
    model=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(side_effect=parse))
    token=REVIEW_CONTEXT.set(snapshot(state))
    try: result=await LLMRAGJudge(model).judge(body,review_query(state),state.current_evidence(),tool_evidence_text(state))
    finally: REVIEW_CONTEXT.reset(token)
    assert not result['publish_allowed'] and result['support']=='unknown'
    assert result['reason']=='judge_failed_or_invalid'
    model.run_structured.assert_awaited_once()


def test_captured_quote_cannot_join_disjoint_source_fragments():
    from pathlib import Path
    value=json.loads((Path(__file__).resolve().parents[1]/
        'acceptance/multi-intent/captured-item-v2-quote-gap-v1.json').read_bytes())
    case=next(case for case in json.loads(DATA.read_bytes())['cases'] if case['case_id']=='MI-07')
    state=fixture_state(case);body,_=candidate(case,bad=True)
    result=assess(state,body,value['arguments'])
    assert not result['publish_allowed'] and result['support']=='unknown'
    assert result['contract_failure_code']=='evidence_quote_not_in_current_source'


@pytest.mark.parametrize('case_id',[f'MI-{index:02d}' for index in range(1,17)])
def test_server_fragments_restore_complete_sources_before_v2_review(case_id):
    from app.agent.item_judge_v2 import to_fragment_verdict,wire_contract
    state,body,value=fixture(case_id)
    wire=to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence())
    wire_contract(snapshot(state)).model_validate(wire)
    assert all(set(ref)=={'fragment_id'} for claim in wire['claims'] for ref in claim['evidence'])
    assert all('evidence_ids' not in check for check in wire['item_checks'])
    result=assess(state,body,wire)
    assert result['publish_allowed'] and not verify_review(state,body,result['item_checks'])[1]
    assert assess(state,body,result)['publish_allowed']


def test_derived_sources_are_owned_by_claim_sections_not_selected_twice():
    from app.agent.item_judge_v2 import to_fragment_verdict
    state,body,value=fixture('MI-13')
    wire=to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence())
    result=assess(state,body,wire)
    assert result['publish_allowed']
    assert result['item_checks'][0]['evidence_ids']==['T1']
    assert result['item_checks'][1]['evidence_ids']==['K1']
    assert result['item_checks'][1]['status']=='waiting_feedback'
    assert not verify_review(state,body,result['item_checks'])[1]


def test_nonfact_greeting_before_item_sections_does_not_own_factual_claims():
    from app.agent.item_judge_v2 import to_fragment_verdict
    state,body,value=fixture('MI-01')
    body='您好，以下逐项回复：\n'+body
    for claim in value['claims']: claim['segment_id']+=1
    value['nonfact_segments']=[{'segment_id':0,'kind':'social'}]
    wire=to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence())
    result=assess(state,body,wire)
    assert result['publish_allowed'] and not verify_review(state,body,result['item_checks'])[1]
    wire['nonfact_segments']=[]
    wire['claims'].insert(0,deepcopy(wire['claims'][1]))
    wire['claims'][0].update(segment_id=0,text='您好，以下逐项回复：')
    assert not assess(state,body,wire)['publish_allowed']


@pytest.mark.parametrize('fault',['omitted_claim','unsupported_claim','preview_complete','omitted_item'])
def test_derived_sources_do_not_replace_semantic_or_completion_checks(fault):
    from app.agent.item_judge_v2 import to_fragment_verdict
    state,body,value=fixture('MI-07')
    wire=to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence())
    if fault=='omitted_claim':
        wire['claims'].pop(1)
        wire['nonfact_segments']=[{'segment_id':1,'kind':'social'}]
    elif fault=='unsupported_claim': wire['claims'][1]['support']='unsupported'
    elif fault=='preview_complete': wire['item_checks'][1]['status']='answered'
    else: wire['item_checks'].pop()
    result=assess(state,body,wire)
    assert not result['publish_allowed']


@pytest.mark.parametrize('fault',['fake','old_version','other_turn','foreign','legacy_quote','duplicate','unreviewed_item_source'])
def test_fragment_ids_cannot_bypass_current_identity_and_item_scope(fault):
    from app.agent.item_judge_v2 import to_fragment_verdict
    state,body,value=fixture('MI-01')
    wire=to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence())
    if fault=='fake': wire['claims'][0]['evidence'][0]['fragment_id']='f'+'0'*64
    elif fault=='old_version': state.evidence_bundle.items[0]['version_id']='new-version'
    elif fault=='other_turn': state.source_message_id=999
    elif fault=='foreign': wire['claims'][1]['evidence']=deepcopy(wire['claims'][2]['evidence'])
    elif fault=='legacy_quote': wire['claims'][0]['evidence'][0]['quote']='assembled text'
    elif fault=='duplicate': wire['claims'][0]['evidence']*=2
    elif fault=='unreviewed_item_source':
        state,body,value=fixture('MI-13')
        wire=to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence())
        wire['item_checks'][1]['evidence_ids']=['K1','U1']
    result=assess(state,body,wire)
    assert not result['publish_allowed']


def test_server_fragment_identity_survives_the_real_privacy_redactor():
    from app.agent.item_judge_v2 import fragments
    from app.services.llm_governance import SensitiveDataRedactor
    state,_,_=fixture('MI-05')
    references=fragments(tool_evidence_text(state),snapshot(state),state.current_evidence())
    redactor=SensitiveDataRedactor()
    for entry in references:
        assert redactor.redact(entry['fragment_id'])==entry['fragment_id']
        assert len(entry['fragment_id'])==65 and entry['fragment_id'].startswith('r')
        assert not any(char.isdigit() for char in entry['fragment_id'])
    assert '[PHONE_REDACTED]' in redactor.redact('联系电话13812345678')


def test_fragment_encoding_is_lossless_including_phone_shaped_hash_digits(monkeypatch):
    from app.agent.item_judge_v2 import fragments
    from app.services.llm_governance import SensitiveDataRedactor
    from types import SimpleNamespace
    from unittest.mock import patch
    state,_,_=fixture('MI-05');context=snapshot(state)
    original='13812345678'+'a'*53
    assert len(original)==64
    with patch('app.agent.item_judge_v2.hashlib.sha256',return_value=SimpleNamespace(hexdigest=lambda:original)):
        result=fragments(tool_evidence_text(state),context,state.current_evidence())
    for entry in result:
        encoded=entry['fragment_id'][1:]
        assert encoded.translate(str.maketrans('abcdefghijklmnop','0123456789abcdef'))==original
        assert SensitiveDataRedactor().redact(entry['fragment_id'])==entry['fragment_id']


def test_captured_fact_nonfact_overlap_is_not_silently_removed():
    from pathlib import Path
    value=json.loads((Path(__file__).resolve().parents[1]/
        'acceptance/multi-intent/captured-v2-overlap-v1.json').read_bytes())
    state,body,_=fixture(value['case_id'])
    raw=value['verdict']
    assert {claim['segment_id'] for claim in raw['claims']} & {item['segment_id'] for item in raw['nonfact_segments']}
    result=assess(state,body,raw)
    assert not result['publish_allowed'] and result['support']=='unknown'
    # Adding a current failure receipt changes the context-bound fragment IDs;
    # the historical receipt must still fail rather than be re-bound silently.
    assert result['contract_failure_code']=='fragment_identity_or_context_invalid'
    from app.agent.item_judge_v2 import to_fragment_verdict
    state,body,current=fixture(value['case_id'])
    current=to_fragment_verdict(current,tool_evidence_text(state),snapshot(state),state.current_evidence())
    current['nonfact_segments'].append({'segment_id':0,'kind':'insufficient_notice'})
    assert assess(state,body,current)['contract_failure_code']=='candidate_segments_not_exactly_reviewed'


@pytest.mark.parametrize('case_id',[f'MI-{index:02d}' for index in range(1,17)])
def test_exclusive_sentence_slots_preserve_v2_item_review(case_id):
    from app.agent.item_judge_v2 import to_fragment_verdict,to_sentence_verdict,sentence_contract
    state,body,value=fixture(case_id)
    value=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    sentence_contract(snapshot(state),body).model_validate(value)
    assert 'claims' not in value and 'nonfact_segments' not in value
    result=assess(state,body,value)
    assert result['publish_allowed'] and not verify_review(state,body,result['item_checks'])[1]
    assert assess(state,body,result)['publish_allowed']


@pytest.mark.parametrize('fault',['missing_slot','extra_slot','both_kinds','nonfact_with_claims','fact_without_claims',
    'claim_outside_source','unsupported_claim','foreign_source','item_missing'])
def test_exclusive_sentence_slots_never_repair_missing_or_conflicting_reviews(fault):
    from app.agent.item_judge_v2 import to_fragment_verdict,to_sentence_verdict
    state,body,value=fixture('MI-01')
    value=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    if fault=='missing_slot': value['segments'].pop('s1')
    elif fault=='extra_slot': value['segments']['s99']={'kind':'social'}
    elif fault=='both_kinds': value['segments']['s1']['kind']=['facts','social']
    elif fault=='nonfact_with_claims': value['segments']['s1']['kind']='social'
    elif fault=='fact_without_claims': value['segments']['s1']['claims']=[]
    elif fault=='claim_outside_source': value['segments']['s1']['claims'][0]['text']='不存在的候选正文'
    elif fault=='unsupported_claim': value['segments']['s1']['claims'][0]['support']='unsupported'
    elif fault=='foreign_source': value['segments']['s1']['claims'][0]['evidence']=deepcopy(value['segments']['s2']['claims'][0]['evidence'])
    else: value['item_checks'].pop()
    assert not assess(state,body,value)['publish_allowed']


def test_permission_failure_can_explain_limitation_but_never_complete_order_query():
    from app.agent.item_judge_v2 import fragments,to_fragment_verdict,to_sentence_verdict
    state,body,value=fixture('MI-05')
    sources=fragments(tool_evidence_text(state),snapshot(state),state.current_evidence())
    failure=next(entry for entry in sources if entry['short_id']=='T1')
    data=json.loads(failure['content'])
    assert data=={'tool':'query_order','success':False,'data':None,'error_type':'permission'}
    wire=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    assert assess(state,body,wire)['publish_allowed']
    wire['item_checks'][0]['status']='answered'
    assert not assess(state,body,wire)['publish_allowed']


def test_failed_order_partial_data_and_error_text_never_become_review_evidence():
    from app.agent.intent_items import catalog
    state,_,_=fixture('MI-05')
    state.tool_calls[0].result.update(data={'status':'private-status','total_amount':'999'},error='private-user-detail')
    state.tool_calls[0].result['error_type']='private-nonstandard-detail'
    entry=catalog(state)['T1']
    assert entry['data']=={'success':False,'data':None,'error_type':'unknown'}
    assert 'private' not in str(entry['data'])


def test_current_flow_limit_is_evidence_of_non_execution_not_completion():
    from app.agent.item_judge_v2 import fragments,to_fragment_verdict,to_sentence_verdict
    state,body,value=fixture('MI-09')
    sources=fragments(tool_evidence_text(state),snapshot(state),state.current_evidence())
    source=next(entry for entry in sources if entry['short_id']=='E2')
    data=json.loads(source['content'])
    assert data['operation_executed'] is False and data['supported_by_current_flow'] is False
    assert data['grants_authorization'] is False
    wire=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    assert assess(state,body,wire)['publish_allowed']
    wire['item_checks'][1]['status']='answered'
    assert not assess(state,body,wire)['publish_allowed']
    state.add_tool_call('create_ticket',{'user_id':101},{'success':True,'data':{'id':'test'}},True)
    assert 'E2' not in snapshot(state)['catalog']


def test_diagnostic_without_resolution_report_cannot_select_answered_in_wire_schema():
    from app.agent.item_judge_v2 import sentence_contract,to_fragment_verdict,to_sentence_verdict
    state,body,value=fixture('MI-13')
    wire=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    wire['item_checks'][1]['status']='answered'
    with pytest.raises(ValueError): sentence_contract(snapshot(state),body).model_validate(wire)


def test_negated_refund_policy_has_current_read_only_proof_without_authorization():
    from app.agent.item_judge_v2 import fragments,to_fragment_verdict,to_sentence_verdict
    state,body,value=fixture('MI-10')
    sources=fragments(tool_evidence_text(state),snapshot(state),state.current_evidence())
    source=next(entry for entry in sources if entry['short_id']=='E2')
    data=json.loads(source['content'])
    assert data['source_kind']=='current_read_only_execution'
    assert not data['operation_executed'] and not data['refund_requested'] and not data['grants_authorization']
    wire=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    assert assess(state,body,wire)['publish_allowed']
    state.add_tool_call('apply_refund',{'user_id':101},{'success':True,'data':{'id':'test'}},True)
    assert 'E2' not in snapshot(state)['catalog']


@pytest.mark.parametrize('case_id,short,error',[('MI-04','L1','missing_order_id'),('MI-06','L2','no_item_evidence')])
def test_current_information_limit_supports_explanation_not_fabricated_business_fact(case_id,short,error):
    from app.agent.item_judge_v2 import fragments,to_fragment_verdict,to_sentence_verdict
    state,body,value=fixture(case_id)
    source=next(entry for entry in fragments(tool_evidence_text(state),snapshot(state),state.current_evidence()) if entry['short_id']==short)
    data=json.loads(source['content'])
    assert data['error_code']==error and not data['grants_authorization']
    if error=='no_item_evidence': assert data['does_not_prove_global_absence']
    wire=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    assert assess(state,body,wire)['publish_allowed']
    index=0 if short=='L1' else 1
    wire['item_checks'][index]['status']='answered'
    assert not assess(state,body,wire)['publish_allowed']
    state.task_runtime.item_observations.clear()
    assert short not in snapshot(state)['catalog']


def test_numbered_generation_headings_are_canonicalized_before_review_without_body_changes():
    from app.agent.intent_items import canonical_item_markers
    from app.agent.item_judge_v2 import to_fragment_verdict,to_sentence_verdict
    state,body,value=fixture('MI-04')
    numbered=body.replace('【i1】','【1】').replace('【i2】','【2】')
    normalized=canonical_item_markers(state,numbered)
    assert normalized==body
    wire=to_sentence_verdict(to_fragment_verdict(value,tool_evidence_text(state),snapshot(state),state.current_evidence()))
    result=assess(state,normalized,wire)
    assert result['publish_allowed'] and not verify_review(state,normalized,result['item_checks'])[1]
    assert canonical_item_markers(state,body)==body


@pytest.mark.parametrize('text',['【1】一\n【1】二','【2】二\n【1】一','【1】一',
    '【i1】一\n【2】二','【1】一\n【2】二\n【3】三','引用【1】一\n【2】二'])
def test_ambiguous_headings_are_never_assigned_to_a_different_item(text):
    from app.agent.intent_items import canonical_item_markers
    state,_,_=fixture('MI-04')
    assert canonical_item_markers(state,text)==text
