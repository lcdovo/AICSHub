"""Evaluation gates must fail before credentials or dispatch, with no business services."""
import hashlib
import json
from pathlib import Path

import pytest
from scripts import continue_business_acceptance as runner
from scripts.business_acceptance import load_cases, digest
from tests.test_business_acceptance import receipt


@pytest.mark.parametrize('fault',['none','wrong_reply','missing_turn','wrong_case','failed_review','changed_review_hash'])
def test_next_case_requires_complete_bound_separate_review(tmp_path,fault):
    case=load_cases()[0][0];turn=case['turns'][0];value=receipt(case,turn)
    value['text_review']['origin']='separate_assistant_review'
    folder=tmp_path/'phase/cases'/case['case_id'];folder.mkdir(parents=True)
    raw=[{'turn_id':'t1','payload':{'reply':value['reply']}}]
    review={'passed':True,'case_id':case['case_id'],'turns':[{'turn_id':'t1','receipt':value}]}
    if fault=='wrong_reply':raw[0]['payload']['reply']='unreviewed reply'
    elif fault=='missing_turn':review['turns']=[]
    elif fault=='wrong_case':review['case_id']='other'
    elif fault=='failed_review':review['passed']=False
    elif fault=='changed_review_hash':value['text_review']['reply_sha256']='bad'
    (folder/'api-receipts.json').write_text(json.dumps(raw),encoding='utf-8')
    (folder/'separate-review.json').write_text(json.dumps(review),encoding='utf-8')
    if fault=='none':runner.prior_review(case,tmp_path/'phase',tmp_path/'continuation')
    else:
        with pytest.raises(ValueError):runner.prior_review(case,tmp_path/'phase',tmp_path/'continuation')


@pytest.mark.parametrize('fault',['none','business_source','unapproved_source','material','old_ledger','runtime'])
def test_repairs_cannot_change_frozen_app_cases_or_old_accounts(tmp_path,monkeypatch,fault):
    phase=tmp_path/'phase';cont=phase/'continuation-v1';cont.mkdir(parents=True)
    data=tmp_path/'acceptance/business-v1';data.mkdir(parents=True)
    material=data/'synthetic.json';material.write_text('{}',encoding='utf-8')
    original={'fingerprint':'original','files':{'app/main.py':'original-app'}}
    current={'fingerprint':'new','files':{'app/main.py':'original-app','scripts/business_http_boundary.py':'repair'}}
    (cont/'original-source.json').write_text(json.dumps(original),encoding='utf-8')
    (phase/'authorization.json').write_text('{}',encoding='utf-8')
    plan={'source_fingerprint':'original','inputs':{'synthetic.json':hashlib.sha256(material.read_bytes()).hexdigest()},
          'protected_ledgers':{'old':'fixed'},'runtime':{'image':'fixed'}}
    plan['prepared_id']=digest(plan)
    if fault=='business_source':current['files']['app/main.py']='changed'
    elif fault=='unapproved_source':current['files']['scripts/unknown.py']='unknown'
    elif fault=='material':material.write_text('{"changed":true}',encoding='utf-8')
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    monkeypatch.setattr(runner,'snapshot_sources',lambda root:current)
    monkeypatch.setattr(runner,'protected_ledgers',lambda:{'old':'changed' if fault=='old_ledger' else 'fixed'})
    monkeypatch.setattr(runner,'runtime_stamp',lambda:{'image':'changed' if fault=='runtime' else 'fixed'})
    monkeypatch.setattr(runner,'require_authorization',lambda *a:None)
    if fault=='none':assert runner.validate_materials(plan,phase,cont)==current
    else:
        with pytest.raises(ValueError):runner.validate_materials(plan,phase,cont)


def test_preflight_artifact_layout_fits_windows_paths():
    # Reproduced setup failure: deeply nested preview + phase hash + nonce >260.
    original=runner.OUT/'live'/('a'*64)/'continuation-v1'/'preflights'/('t'*65)/'continuation-v1/private'/('b'*32+'.env')
    shortened=runner.OUT/'preflights-c1'/('t'*65)/'continuation-v1/private'/('b'*32+'.env')
    assert len(str(original.resolve()))>260
    assert len(str(shortened.resolve()))<240


def test_bundle_only_copies_fingerprinted_inputs_and_rejects_shadow(tmp_path):
    original={'files':{'scripts/runner.py':'one','tests/t.py':'two','acceptance/source.json':'three','app/main.py':'app'},'unsupported_paths':[]}
    material=runner.bundle_material(original)
    assert set(material)=={'scripts/runner.py','tests/t.py','acceptance/source.json'}
    for folder in ['scripts','tests','acceptance']:(tmp_path/folder).mkdir()
    for name in material:(tmp_path/name).write_text(name,encoding='utf-8')
    material={k:hashlib.sha256((tmp_path/k).read_bytes()).hexdigest() for k in material}
    runner.validate_bundle(tmp_path,material)
    (tmp_path/'app').mkdir()
    with pytest.raises(ValueError,match='shadow'):runner.validate_bundle(tmp_path,material)


def test_bundle_refuses_unfingerprinted_secret_file(tmp_path):
    for folder in ['scripts','tests','acceptance']:(tmp_path/folder).mkdir()
    (tmp_path/'scripts/.env').write_text('synthetic-secret',encoding='utf-8')
    with pytest.raises(ValueError,match='bundle_changed'):runner.validate_bundle(tmp_path,{})


def test_unsupported_source_is_rejected_before_copy():
    with pytest.raises(ValueError,match='unsupported_source'):runner.bundle_material({'files':{},'unsupported_paths':['scripts/link.py']})


def test_concurrent_preflights_publish_only_complete_bundle(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    source=tmp_path/'source';source.mkdir()
    for folder in ['scripts','tests','acceptance']:
        (source/folder).mkdir()
        for i in range(4):(source/folder/f'f{i}.py').write_text('synthetic '+str(i),encoding='utf-8')
    material={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*.py')}
    bundle=tmp_path/'bundle';barrier=Barrier(4)
    def build():
        barrier.wait()
        runner.create_bundle(bundle,material,source)
        runner.validate_bundle(bundle,material)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:build(),range(4)))
    assert not list(tmp_path.glob('c1-stage-*'))


@pytest.mark.parametrize('field',['confirmed','stream_with_usage_footer_authorized'])
def test_revoked_continuation_or_stream_permission_fails_closed(field):
    auth={'confirmed':True,'continuation_id':'fixed','max_new_dispatches':148,'user_statement':'synthetic approval','stream_with_usage_footer_authorized':True}
    proposal={'continuation_id':'fixed'}
    runner.require_continuation_authorization(auth,proposal)
    auth[field]=False
    with pytest.raises(ValueError,match='continuation_authorization'):
        runner.require_continuation_authorization(auth,proposal)


@pytest.mark.parametrize('fault',['no_tests','incomplete_matrix','wrong_image','bad_hash'])
def test_live_admission_requires_regression_and_four_path_matrix(tmp_path,monkeypatch,fault):
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    reports=tmp_path/'reports';reports.mkdir()
    current={'fingerprint':'fixed','files':{name:'hash' for name in ['scripts/business_http_boundary.py','scripts/business_acceptance_execute.py','scripts/continue_business_acceptance.py']}}
    plan={'runtime':{'image':'deployed','app_hashes':{}}}
    binding={'source_fingerprint':'fixed','test_evidence':[],'matrix_evidence':[]}
    if fault!='no_tests':
        for role in ['host','image']:
            folder=reports/role;folder.mkdir()
            source=folder/'source-start.json';source.write_text(json.dumps({'files':current['files']}),encoding='utf-8')
            manifest={'exit_code':0,'source_tree_stable':True,'source_snapshot_complete':True,'blocked_network_operations':0,'image_id':'wrong' if fault=='wrong_image' else 'deployed'}
            path=folder/'manifest.json';path.write_text(json.dumps(manifest),encoding='utf-8')
            binding['test_evidence'].append({'role':role,'path':path.relative_to(tmp_path).as_posix(),'sha256':'bad' if fault=='bad_hash' else hashlib.sha256(path.read_bytes()).hexdigest(),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    with pytest.raises(ValueError):runner.validate_admission(binding,plan,current)
