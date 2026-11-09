"""Preflight-first continuation in the original account; no business deployment."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.business_acceptance import OUT,load_cases,operations,require_authorization,digest,runtime_stamp,protected_ledgers,score_turn
from scripts.test_provenance import snapshot_sources

EXTENSIONS={
    'scripts/business_http_boundary.py','tests/test_business_http_boundary.py',
    'scripts/business_acceptance_execute.py','scripts/continue_business_acceptance.py',
    'tests/test_business_execution_audit.py',
}


def rows(path, operations):
    with sqlite3.connect('file:'+str(path.resolve()).replace('\\','/')+'?mode=ro',uri=True) as db:
        return db.execute('SELECT * FROM dispatches WHERE operation IN ('+','.join('?' for _ in operations)+') ORDER BY operation',tuple(operations)).fetchall()


def validate_materials(plan, phase, cont):
    if digest({k:v for k,v in plan.items() if k!='prepared_id'})!=plan['prepared_id']:
        raise ValueError('original_plan_changed')
    original=json.loads((cont/'original-source.json').read_text(encoding='utf-8'))
    current=snapshot_sources(ROOT)
    if original['fingerprint']!=plan['source_fingerprint']:
        raise ValueError('original_source_checkpoint_invalid')
    changed={k for k in original['files'] if current['files'].get(k)!=original['files'][k]}
    added=set(current['files'])-set(original['files'])
    if changed or not added.issubset(EXTENSIONS):
        raise ValueError('business_or_unapproved_source_change')
    if {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'acceptance/business-v1').glob('*.json')}!=plan['inputs']:
        raise ValueError('frozen_case_material_changed')
    if protected_ledgers()!=plan['protected_ledgers']:
        raise ValueError('old_protected_ledger_changed')
    if runtime_stamp()!=plan['runtime']:
        raise ValueError('deployed_app_or_provider_changed')
    require_authorization(plan,json.loads((phase/'authorization.json').read_text(encoding='utf-8')))
    return current


def prior_review(case, phase, cont):
    directory=cont/'cases'/case['case_id']
    if not directory.exists():directory=phase/'cases'/case['case_id']
    review=json.loads((directory/'separate-review.json').read_text(encoding='utf-8'))
    raw=json.loads((directory/'api-receipts.json').read_text(encoding='utf-8'))
    if review.get('passed') is not True or review.get('case_id')!=case['case_id']:
        raise ValueError('previous_case_review_failed')
    by_id={r['turn_id']:r for r in raw}
    for turn, entry in zip(case['turns'],review.get('turns',[])):
        if entry['turn_id']!=turn['turn_id'] or entry['receipt']['reply']!=by_id[turn['turn_id']]['payload']['reply']:
            raise ValueError('previous_text_review_unbound')
        if not score_turn(case,turn,entry['receipt'])['passed']:
            raise ValueError('previous_case_oracle_failed')
    if len(review.get('turns',[]))!=len(case['turns']):
        raise ValueError('previous_case_review_incomplete')


def bundle_material(current):
    if current.get('unsupported_paths'):
        raise ValueError('unsupported_source_path')
    return {k:v for k,v in current['files'].items() if k.startswith(('scripts/','tests/','acceptance/'))}


def validate_bundle(bundle, material):
    if {p.name for p in bundle.iterdir()}!={'scripts','tests','acceptance'}:
        raise ValueError('unexpected_bundle_root_or_app_shadow')
    actual={p.relative_to(bundle).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in bundle.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if actual!=material or any(p.is_symlink() for p in bundle.rglob('*')):
        raise ValueError('frozen_execution_bundle_changed')


def create_bundle(bundle, material, source_root):
    if bundle.exists():
        validate_bundle(bundle,material)
        return
    stage=bundle.parent/('c1-stage-'+uuid.uuid4().hex)
    stage.mkdir()
    try:
        for relative, expected in material.items():
            source=source_root/relative
            if source.is_symlink() or not source.resolve().is_relative_to(source_root.resolve()):raise ValueError('source_escape')
            if hashlib.sha256(source.read_bytes()).hexdigest()!=expected:raise ValueError('source_changed_during_copy')
            target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
        validate_bundle(stage,material)
        try:
            stage.rename(bundle)
        except OSError:
            if not bundle.exists():raise
            validate_bundle(bundle,material)
    finally:
        if stage.exists():
            resolved=stage.resolve()
            if resolved.parent!=bundle.parent.resolve() or not resolved.name.startswith('c1-stage-'):
                raise ValueError('unsafe_temporary_bundle_cleanup')
            shutil.rmtree(resolved)
    validate_bundle(bundle,material)


def require_continuation_authorization(auth, proposal):
    if (auth.get('confirmed') is not True or auth.get('continuation_id')!=proposal['continuation_id']
            or auth.get('max_new_dispatches')!=148 or not auth.get('user_statement')
            or auth.get('stream_with_usage_footer_authorized') is not True):
        raise ValueError('explicit_continuation_authorization_required')


def validate_admission(binding, plan, current):
    if binding.get('source_fingerprint')!=current['fingerprint']:
        raise ValueError('repaired_evaluation_source_changed')
    def evidence(entry):
        path=(ROOT/entry['path']).resolve()
        if not path.is_relative_to((ROOT/'reports').resolve()):raise ValueError('evidence_path_outside_reports')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('evidence_changed')
        return path,json.loads(path.read_text(encoding='utf-8'))
    expected_hashes={name:current['files'][name] for name in ['scripts/business_http_boundary.py','scripts/business_acceptance_execute.py','scripts/continue_business_acceptance.py']}
    tests=binding.get('test_evidence',[])
    if {entry.get('role') for entry in tests}!={'host','image'} or len(tests)!=2:
        raise ValueError('host_and_image_regressions_required')
    for entry in tests:
        path,manifest=evidence(entry)
        if (manifest.get('exit_code')!=0 or not manifest.get('source_tree_stable') or not manifest.get('source_snapshot_complete')
                or manifest.get('blocked_network_operations')!=0):raise ValueError('regression_not_admitted')
        snapshot_path=path.parent/'source-start.json'
        if hashlib.sha256(snapshot_path.read_bytes()).hexdigest()!=entry['source_sha256']:raise ValueError('regression_source_changed')
        snapshot=json.loads(snapshot_path.read_text(encoding='utf-8'))
        if {k:v for k,v in snapshot['files'].items() if k.startswith(('scripts/','tests/','acceptance/'))}!=bundle_material(current):
            raise ValueError('regression_not_for_current_inputs')
        if entry['role']=='image':
            if manifest.get('image_id')!=plan['runtime']['image']:raise ValueError('wrong_test_image')
            if {k:v for k,v in snapshot['files'].items() if k.startswith('app/')}!=plan['runtime']['app_hashes']:
                raise ValueError('regression_not_for_deployed_app')
    matrix=binding.get('matrix_evidence',[])
    expected={'single-rest','single-ws','multi-rest','multi-ws'}
    seen=set()
    for entry in matrix:
        _,report=evidence(entry)
        variant=report.get('variant')
        if (variant not in expected or variant in seen or report.get('mode')!='mock' or report.get('paid_new')!=0
                or report.get('status')!='AWAITING_SEPARATE_TEXT_REVIEW' or report.get('error') is not None
                or report.get('source_fingerprint')!=current['fingerprint'] or report.get('execution_hashes')!=expected_hashes):
            raise ValueError('runtime_matrix_not_admitted')
        if report['new_dispatches']!=(6 if variant.startswith('single') else 3):raise ValueError('runtime_matrix_missing_sdk_path')
        seen.add(variant)
    if seen!=expected:raise ValueError('complete_runtime_matrix_required')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=('mock','live'),required=True)
    parser.add_argument('--case',required=True)
    parser.add_argument('--variant',choices=('case','single-rest','single-ws','multi-rest','multi-ws'),default='case')
    parser.add_argument('--label',default='preflight')
    args=parser.parse_args()
    plan=json.loads((OUT/'plan.json').read_text(encoding='utf-8'))
    phase=OUT/'live'/plan['prepared_id'];cont=phase/'continuation-v1'
    proposal=json.loads((phase/'continuation-proposal.json').read_text(encoding='utf-8'))
    auth=json.loads((cont/'authorization.json').read_text(encoding='utf-8'))
    require_continuation_authorization(auth,proposal)
    current=validate_materials(plan,phase,cont)
    if digest(rows(phase/'dispatch.sqlite',auth['old_operations']))!=proposal['old_nine_rows_sha256']:
        raise ValueError('old_nine_rows_changed')
    cases,_=load_cases();case=next(c for c in cases if c['case_id']==args.case)
    if case['case_id'] in {'BA-01','BA-02','BA-03'} and args.mode=='live':
        raise ValueError('passed_case_slots_retired')
    if args.mode=='live':
        if args.variant!='case':raise ValueError('live_override_forbidden')
        if (cont/'STOP.json').exists():raise ValueError('continuation_stopped')
        binding=json.loads((cont/'execution-binding.json').read_text(encoding='utf-8'))
        validate_admission(binding,plan,current)
        for previous in cases[:cases.index(case)]:prior_review(previous,phase,cont)
        if (cont/'cases'/case['case_id']).exists():raise ValueError('case_already_started_no_replay')
    nonce=uuid.uuid4().hex
    lock=cont/'running.lock'
    if args.mode=='live':
        with lock.open('x',encoding='utf-8') as stream:stream.write(nonce)
    envpath=None;container=None;key=None
    try:
        if args.mode=='mock':
            safe_label=''.join(c for c in args.label if c.isalnum() or c=='-')[:40]
            evaluation=OUT/'preflights-c1'/(datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-'+safe_label)
            evaluation.mkdir(parents=True)
            shutil.copy2(phase/'dispatch.sqlite',evaluation/'dispatch.sqlite')
            shutil.copy2(phase/'dispatch.created.json',evaluation/'dispatch.created.json')
            shutil.copy2(phase/'plan.json',evaluation/'plan.json')
            shutil.copy2(phase/'authorization.json',evaluation/'authorization.json')
            shutil.copy2(phase/'continuation-proposal.json',evaluation/'continuation-proposal.json')
            (evaluation/'cases/BA-04').mkdir(parents=True)
            shutil.copy2(phase/'cases/BA-04/provider-responses.json',evaluation/'cases/BA-04/provider-responses.json')
            (evaluation/'continuation-v1').mkdir()
            shutil.copy2(cont/'authorization.json',evaluation/'continuation-v1/authorization.json')
        else:evaluation=phase
        bundle=OUT/('c1b-'+current['fingerprint'][:12])
        material=bundle_material(current)
        create_bundle(bundle,material,ROOT)
        private=evaluation/'continuation-v1/private';private.mkdir(parents=True,exist_ok=True)
        env={**plan['runtime']['provider'],'DATABASE_URL_OVERRIDE':'sqlite+aiosqlite:////tmp/evaluation.db',
            'PYTHONPATH':'/verification:/app','PYTHONIOENCODING':'utf-8','REDIS_ENABLED':'false','USE_MILVUS':'false',
            'AUTH_DEMO_ACCOUNTS_ENABLED':'false','USER_MEMORY_ENABLED':'false','LANGFUSE_ENABLED':'false','MCP_ENABLED':'false',
            'REQUEST_UNDERSTANDING_MODE':'model','REQUEST_UNDERSTANDING_PERCENTAGE':'100',
            'AGENT_TASK_LOOP_ENABLED':'true','AGENT_ITEM_COMPLETION_ENABLED':'true','AGENT_TASK_REFUND_PREVIEW_ENABLED':'true',
            'LLM_GATEWAY_ENABLED':'true','LLM_MAX_TOKENS':'2048','LLM_MAX_RETRIES':'0',
            'LLM_BACKUP_BASE':'','LLM_BACKUP_MODEL':'','LLM_LOCAL_BASE':'','LLM_LOCAL_MODEL':'',
            'LLM_DAILY_USER_TOKEN_BUDGET':str(plan['max_input_bytes_reserved']+plan['max_output_tokens']),
            'LLM_DAILY_USER_COST_USD':'5.6','LLM_MAX_COST_USD':'0.2',
            'LLM_INPUT_PRICE_PER_1K_USD':'0.002','LLM_OUTPUT_PRICE_PER_1K_USD':'0.008','LLM_PRICE_VERIFIED':'false',
            'LLM_REQUEST_TIMEOUT_SECONDS':'30','RERANKER_LOCAL_BATCH_SIZE':'20'}
        if args.mode=='live':
            from dotenv import get_key
            key=get_key(ROOT/'.env','LLM_API_KEY')
            if not key:raise ValueError('provider_key_missing')
        env['LLM_API_KEY']=key or 'synthetic-only'
        env['AICSHUB_EVAL_SOURCE_FINGERPRINT']=current['fingerprint']
        if any('\n' in str(value) or '\r' in str(value) for value in env.values()):raise ValueError('multiline_env_value')
        envpath=private/(nonce+'.env')
        if os.name=='nt' and len(str(envpath.resolve()))>240:raise ValueError('evaluation_path_too_long_before_credentials_write')
        envpath.write_text('\n'.join(k+'='+str(v) for k,v in env.items())+'\n',encoding='utf-8')
        container='aicshub-business-eval-'+nonce
        bootstrap="""import subprocess,sys,os
subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links=/wheels','aiosqlite==0.22.1'],check=True)
os.execv(sys.executable,[sys.executable,'/verification/scripts/business_acceptance_execute.py','--mode',sys.argv[1],'--case',sys.argv[2],'--variant',sys.argv[3]])
"""
        cmd=['docker','run','--rm','--name',container,'--label','aicshub.eval.owner='+nonce,
            '--network','none' if args.mode=='mock' else 'bridge','--user','root','--workdir','/tmp',
            '--entrypoint','python','--env-file',str(envpath),
            '--mount',f'type=bind,source={bundle},target=/verification,readonly',
            '--mount',f'type=bind,source={evaluation},target=/evaluation',
            '--mount',f'type=bind,source={ROOT/"reports/offline-wheel-cache"},target=/wheels,readonly',
            plan['runtime']['image'],'-c',bootstrap,args.mode,args.case,args.variant]
        try:
            run=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',
                env={**os.environ,'PYTHONIOENCODING':'utf-8'},timeout=180)
            output=run.stdout+run.stderr
            if key:output=output.replace(key,'<REDACTED>')
            (private/(case['case_id']+'-'+args.variant+'-container.log')).write_text(output,encoding='utf-8')
            for line in run.stdout.splitlines():
                if line.startswith('CASE_RESULT='):print(line)
            if run.returncode and not any(x.startswith('CASE_RESULT=') for x in run.stdout.splitlines()):
                raise RuntimeError('container_setup_or_execution_failed')
            print('EVALUATION_ROOT='+str(evaluation))
            return run.returncode
        except BaseException as exc:
            if args.mode=='live':
                (cont/'STOP.json').write_text(json.dumps({'case_id':case['case_id'],'reason':'host_failure:'+type(exc).__name__}))
            raise
    finally:
        if container:
            found=subprocess.run(['docker','inspect','--format','{{index .Config.Labels "aicshub.eval.owner"}}',container],capture_output=True,text=True,encoding='utf-8')
            if found.returncode==0 and found.stdout.strip()==nonce:
                subprocess.run(['docker','rm','-f',container],capture_output=True,check=True)
        if envpath:envpath.unlink(missing_ok=True)
        if args.mode=='live' and lock.exists() and lock.read_text(encoding='utf-8')==nonce:lock.unlink()


if __name__=='__main__':raise SystemExit(main())
