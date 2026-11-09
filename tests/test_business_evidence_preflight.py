"""Capture the real safe handoff and reject incomplete evaluation policy before calls."""
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from scripts.business_acceptance import load_cases
from scripts.business_evidence_preflight import audit_refund_sources,require_refund_sources
from scripts.business_api_adapter import api_case
from app.config.config import Settings,settings
from scripts.offline_profiles import ACTIVE_CONFIG
from tests.understanding_wire_fixture import item_wire

ROOT=Path(__file__).resolve().parents[1]


def test_original_positive_refund_material_is_rejected_before_dispatch():
    cases,sources=load_cases()
    review=json.loads((ROOT/'acceptance/business-v2/refund-source-review.json').read_text(encoding='utf-8'))
    findings=audit_refund_sources(cases,sources,review)
    assert {x['case_id'] for x in findings if not x['passed']}=={'BA-05','BA-07','BA-10','BA-14','BA-17'}
    with pytest.raises(ValueError,match='evidence_incomplete'):require_refund_sources(cases,sources,review)


def test_hash_bound_scope_review_cannot_survive_content_changes():
    cases,sources=load_cases();sources=copy.deepcopy(sources)
    review=json.loads((ROOT/'acceptance/business-v2/refund-source-review.json').read_text(encoding='utf-8'))
    sources['items'][0]['content']+='changed'
    with pytest.raises(ValueError,match='source_content_changed'):audit_refund_sources(cases,sources,review)


def test_separate_candidate_covers_reviewed_policy_scopes_without_relabeling_old_cases():
    def read(name):return json.loads((ROOT/'acceptance/business-v2'/name).read_text(encoding='utf-8'))
    cases=read('refund-cases-candidate.json')['cases']
    findings=require_refund_sources(cases,read('refund-sources-candidate.json'),read('refund-source-review-candidate.json'))
    assert len(findings)==5 and all(row['passed'] for row in findings)
    originals,_=load_cases()
    for candidate,original in zip(cases,originals):
        assert candidate['turns']==original['turns']
        assert {k:v for k,v in candidate.items() if k!='source_ids'}=={k:v for k,v in original.items() if k!='source_ids'}


def test_captured_short_reason_now_prompts_before_old_handoff_decisions(tmp_path,monkeypatch):
    config=Settings(_env_file=None,**ACTIVE_CONFIG)
    for key in ACTIVE_CONFIG:monkeypatch.setattr(settings,key,getattr(config,key))
    cases,sources=load_cases();case=next(c for c in cases if c['case_id']=='BA-05')
    captured=json.loads((ROOT/'tests/fixtures/business-refund-handoff-v1.json').read_text(encoding='utf-8'))
    replies=iter(copy.deepcopy(captured))
    assert captured[0]['read_tasks']==[]
    def install(service,patch):
        patch.setattr(service,'is_available',lambda:True)
        async def structured(*args,**kwargs):return item_wire(next(replies))
        patch.setattr(service,'run_structured',AsyncMock(side_effect=structured))
    with api_case(case,tmp_path,sources,install_model=install) as (send,_):
        row=send(case['turns'][0])
    assert row['payload']['task_status']=='waiting_input'
    assert row['task']['waiting_for']=='reason'
    assert row['effects']=={'simulated_applications':0,'preview_count':0}
    assert [step['action'] for step in row['task']['steps']]==['read','ask_user']
    assert '具体原因' in row['payload']['reply']
    assert next(replies)==captured[1]  # Captured search/handoff responses remain unused, not rewritten.
