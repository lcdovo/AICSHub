"""Bounded refund revalidation in the existing account, without business deployment."""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.business_acceptance import OUT, digest, load_cases, operations, protected_ledgers, runtime_stamp
from scripts.continue_business_acceptance import create_bundle, bundle_material, prior_review, rows
from scripts.test_provenance import snapshot_sources

PHASE = OUT / 'live' / '1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042'
REV = PHASE / 'refund-scope-v2'
FILES = ['scripts/business_http_boundary.py', 'scripts/business_refund_execute.py', 'scripts/revalidate_business_refund.py']
TESTS = ['tests/test_refund_read_plan_scope.py', 'tests/test_business_http_boundary.py',
         'tests/test_business_execution_audit.py', 'tests/test_business_refund_revision.py']


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_revision_authorization(auth, proposal):
    if digest({k:v for k,v in proposal.items() if k != 'continuation_id'}) != proposal['continuation_id']:
        raise ValueError('revision_proposal_changed')
    if (auth.get('confirmed') is not True or auth.get('approved_by') != 'user'
            or auth.get('continuation_id') != proposal['continuation_id']
            or auth.get('max_new_dispatches') != 24 or not auth.get('user_statement')
            or auth.get('stream_with_usage_footer_authorized') is not True):
        raise ValueError('explicit_refund_revision_authorization_required')


def validate_admission(binding, proposal):
    if (binding.get('source_fingerprint') != proposal['source_fingerprint']
            or binding.get('executor_sha256') != sha(ROOT/'scripts/business_refund_execute.py')):
        raise ValueError('preflight_source_changed')
    roles=set();variants=set()
    for entry in binding['evidence']:
        path=(ROOT/entry['path']).resolve()
        if not path.is_relative_to((ROOT/'reports').resolve()) or sha(path)!=entry['sha256']:
            raise ValueError('preflight_evidence_changed')
        value=read(path)
        if entry['role'] in ['host','image']:
            if (entry['role'] in roles or value.get('exit_code')!=0
                    or value.get('source_tree_stable') is not True
                    or value.get('source_snapshot_complete') is not True
                    or value.get('blocked_network_operations')!=0):
                raise ValueError('regression_not_admitted')
            snapshot=read(path.parent/'source-start.json')
            current=snapshot_sources(ROOT)
            if bundle_material(snapshot)!=bundle_material(current):
                raise ValueError('regression_inputs_changed')
            expected=proposal['candidate_app_hashes'] if entry['role']=='image' else {
                k:v for k,v in current['files'].items() if k.startswith('app/')}
            if {k:v for k,v in snapshot['files'].items() if k.startswith('app/')}!=expected:
                raise ValueError('regression_app_changed')
            if entry['role']=='image' and value.get('image_id')!=proposal['runtime']['image']:
                raise ValueError('regression_image_changed')
            roles.add(entry['role'])
        elif entry['role']=='matrix':
            variant=value.get('variant')
            if (value.get('mode')!='mock' or value.get('paid_new')!=0 or value.get('error') is not None
                    or value.get('status')!='AWAITING_SEPARATE_TEXT_REVIEW'
                    or value.get('source_fingerprint')!=proposal['source_fingerprint']
                    or value.get('new_dispatches')!=(8 if variant.startswith('single') else 3)
                    or variant in variants):raise ValueError('matrix_not_admitted')
            variants.add(variant)
        else:raise ValueError('unexpected_evidence_role')
    if roles!={'host','image'} or variants!={'single-rest','single-ws','multi-rest','multi-ws'}:
        raise ValueError('complete_preflight_required')


def verify_proposal(proposal):
    if digest({k:v for k,v in proposal.items() if k != 'continuation_id'}) != proposal['continuation_id']:
        raise ValueError('revision_proposal_changed')
    require_revision_authorization(read(REV/'authorization.json'), proposal)
    if digest(rows(PHASE/'dispatch.sqlite', proposal['old_operations'])) != proposal['old_rows_sha256']:
        raise ValueError('original_sixteen_rows_changed')
    if protected_ledgers() != proposal['protected_ledgers']:
        raise ValueError('historical_account_changed')
    if runtime_stamp() != proposal['runtime']:
        raise ValueError('business_runtime_changed')
    if snapshot_sources(ROOT)['fingerprint'] != proposal['source_fingerprint']:
        raise ValueError('candidate_source_changed')
    if sha(REV/'overlay/request_understanding.py') != proposal['overlay_sha256']:
        raise ValueError('candidate_overlay_changed')
    if any(sha(PHASE/p) != value for p,value in proposal['historical_stops'].items()):
        raise ValueError('historical_stop_changed')


def prepare():
    if REV.exists():
        raise ValueError('revision_already_prepared_no_reset')
    plan=read(PHASE/'plan.json')
    runtime=runtime_stamp()
    if runtime != plan['runtime']:
        raise ValueError('deployed_baseline_changed')
    with sqlite3.connect(PHASE/'dispatch.sqlite') as db:
        old=[r[0] for r in db.execute('SELECT operation FROM dispatches ORDER BY operation')]
    if len(old) != 16:
        raise ValueError('expected_sixteen_original_dispatches')
    candidate=ROOT/'app/agent/request_understanding.py'
    def assignment(text):
        return next(n for n in ast.parse(text).body if isinstance(n,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=='ITEM_UNDERSTANDING_PROMPT' for t in n.targets))
    # Read the immutable deployed file. Preserve every byte outside the one prompt assignment.
    result=subprocess.run(['docker','exec','ai-customer-app-1','python','-c',
        "import base64;from pathlib import Path;print(base64.b64encode(Path('app/agent/request_understanding.py').read_bytes()).decode())"],
        capture_output=True,text=True,check=True)
    original=base64.b64decode(result.stdout.strip())
    if hashlib.sha256(original).hexdigest()!=runtime['app_hashes']['app/agent/request_understanding.py']:
        raise ValueError('deployed_prompt_source_changed')
    node=assignment(original.decode('utf-8'))
    new_prompt=ast.literal_eval(assignment(candidate.read_text(encoding='utf-8')).value)
    lines=original.splitlines(keepends=True)
    replacement=('ITEM_UNDERSTANDING_PROMPT = '+repr(new_prompt)+'\n').encode('utf-8')
    patched=b''.join(lines[:node.lineno-1])+replacement+b''.join(lines[node.end_lineno:])
    original_tree=ast.parse(original.decode('utf-8'));patched_tree=ast.parse(patched.decode('utf-8'))
    for tree in [original_tree,patched_tree]:
        target=next(n for n in tree.body if isinstance(n,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=='ITEM_UNDERSTANDING_PROMPT' for t in n.targets))
        target.value=ast.Constant('PROMPT_ONLY_CHANGE')
    if ast.dump(original_tree)!=ast.dump(patched_tree):raise ValueError('non_prompt_overlay_change')
    REV.mkdir()
    overlay=REV/'overlay/request_understanding.py';overlay.parent.mkdir()
    overlay.write_bytes(patched)
    expected=dict(runtime['app_hashes']);expected['app/agent/request_understanding.py']=sha(overlay)
    allowed=operations(load_cases()[0])
    allowed.update({'R2/'+case+'/'+turn+'/http-'+str(i):2048
                    for case in ['BA-05','BA-10'] for turn in ['t1','t2'] for i in range(1,7)})
    proposal={'baseline_requests':16,'max_new_dispatches':24,'cases':['BA-05','BA-10'],
              'old_operations':old,'old_rows_sha256':digest(rows(PHASE/'dispatch.sqlite',old)),
              'cached_replay_only_exact_body_hashes':{},'allowed_operations':allowed,
              'runtime':runtime,'candidate_app_hashes':expected,'overlay_sha256':sha(overlay),
              'source_fingerprint':snapshot_sources(ROOT)['fingerprint'],
              'protected_ledgers':protected_ledgers(),
              'historical_stops':{p:sha(PHASE/p) for p in ['STOP.json','continuation-v1/STOP.json']},
              'stop_on_first_failure':True,'automatic_retry':False,'deployment':False,
              'scope':'new prompt; old passes historical; synthetic SQL/orders, frozen knowledge, substituted Redis',
              'versions':{'REQUEST_UNDERSTANDING_VERSION':'understanding.read-plan-scope.v2',
                          'LLM_PROMPT_VERSION':'read-plan-scope.v2'}}
    proposal['continuation_id']=digest(proposal)
    save(REV/'proposal.json',proposal)
    save(REV/'authorization.json',{'confirmed':True,'approved_by':'user',
         'user_statement':'可以开始吧，遇到错误自己先修复',
         'continuation_id':proposal['continuation_id'],'max_new_dispatches':24,
         'stream_with_usage_footer_authorized':True,
         'interpretation':'bounded next step: preview-cancel and preview-confirm; existing account only; first failure stops'})
    print('PREPARED='+proposal['continuation_id'])


def run(mode, case, variant='case'):
    proposal=read(REV/'proposal.json');verify_proposal(proposal)
    if mode=='live':
        if case not in proposal['cases'] or variant!='case' or (REV/'STOP.json').exists():
            raise ValueError('stopped_or_unapproved_case')
        binding=read(REV/'execution-binding.json')
        validate_admission(binding,proposal)
        if case=='BA-10':
            prior_review(next(c for c in load_cases()[0] if c['case_id']=='BA-05'),PHASE,REV)
        if (REV/'cases'/case).exists():raise ValueError('case_already_started_no_retry')
        from scripts.business_evidence_preflight import require_refund_sources
        cases,sources=load_cases()
        require_refund_sources([c for c in cases if c['case_id']==case], sources,
                               read(ROOT/proposal.get('source_review','acceptance/business-v2/refund-source-review.json')))
        evaluation=PHASE
    else:
        evaluation=OUT/('r2mock-'+uuid.uuid4().hex[:12]);evaluation.mkdir()
        for name in ['dispatch.sqlite','dispatch.created.json','plan.json','authorization.json']:
            shutil.copy2(PHASE/name,evaluation/name)
        folder=evaluation/REV.name;folder.mkdir()
        for name in ['proposal.json','authorization.json']:shutil.copy2(REV/name,folder/name)
        (evaluation/'cases/BA-04').mkdir(parents=True)
        shutil.copy2(PHASE/'cases/BA-04/provider-responses.json',evaluation/'cases/BA-04/provider-responses.json')
    material=bundle_material(snapshot_sources(ROOT))
    bundle=OUT/('r2b-'+proposal['source_fingerprint'][:12]);create_bundle(bundle,material,ROOT)
    plan=read(PHASE/'plan.json')
    env={**plan['runtime']['provider'],**proposal['versions'],
         'DATABASE_URL_OVERRIDE':'sqlite+aiosqlite:////tmp/evaluation.db',
         'PYTHONPATH':'/verification:/app','PYTHONIOENCODING':'utf-8',
         'REDIS_ENABLED':'false','USE_MILVUS':'false','AUTH_DEMO_ACCOUNTS_ENABLED':'false',
         'USER_MEMORY_ENABLED':'false','LANGFUSE_ENABLED':'false','MCP_ENABLED':'false',
         'REQUEST_UNDERSTANDING_MODE':'model','REQUEST_UNDERSTANDING_PERCENTAGE':'100',
         'AGENT_TASK_LOOP_ENABLED':'true','AGENT_ITEM_COMPLETION_ENABLED':'true',
         'AGENT_TASK_REFUND_PREVIEW_ENABLED':'true','LLM_GATEWAY_ENABLED':'true',
         'LLM_MAX_TOKENS':'2048','LLM_MAX_RETRIES':'0','LLM_BACKUP_BASE':'','LLM_BACKUP_MODEL':'',
         'LLM_LOCAL_BASE':'','LLM_LOCAL_MODEL':'','LLM_REQUEST_TIMEOUT_SECONDS':'30',
         'LLM_DAILY_USER_TOKEN_BUDGET':str(plan['max_input_bytes_reserved']+plan['max_output_tokens']),
         'LLM_DAILY_USER_COST_USD':'5.6','LLM_MAX_COST_USD':'0.2',
         'LLM_INPUT_PRICE_PER_1K_USD':'0.002','LLM_OUTPUT_PRICE_PER_1K_USD':'0.008',
         'LLM_PRICE_VERIFIED':'false','RERANKER_LOCAL_BATCH_SIZE':'20',
         'AICSHUB_EVAL_SOURCE_FINGERPRINT':proposal['source_fingerprint']}
    env['AICSHUB_EVAL_REVISION']=REV.name
    nonce=uuid.uuid4().hex;container='aicshub-refund-eval-'+nonce
    private=OUT/'r2-private';private.mkdir(exist_ok=True)
    envpath=private/(nonce+'.env');lock=REV/'running.lock';key=None
    if mode=='live':
        with lock.open('x') as stream:stream.write(nonce)
    try:
        if mode=='live':
            from dotenv import get_key
            key=get_key(ROOT/'.env','LLM_API_KEY')
            if not key:raise ValueError('missing_provider_key')
        env['LLM_API_KEY']=key or 'synthetic-only'
        if any('\n' in str(v) or '\r' in str(v) for v in env.values()):raise ValueError('multiline_environment')
        envpath.write_text('\n'.join(k+'='+str(v) for k,v in env.items())+'\n',encoding='utf-8')
        bootstrap="""import subprocess,sys,os
subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links=/wheels','aiosqlite==0.22.1'],check=True)
os.execv(sys.executable,[sys.executable,'/verification/scripts/business_refund_execute.py','--mode',sys.argv[1],'--case',sys.argv[2],'--variant',sys.argv[3]])
"""
        cmd=['docker','run','--rm','--name',container,'--label','aicshub.eval.owner='+nonce,
             '--network','none' if mode=='mock' else 'bridge','--user','root','--workdir','/tmp',
             '--entrypoint','python','--env-file',str(envpath),
             '--mount',f'type=bind,source={bundle},target=/verification,readonly',
             '--mount',f'type=bind,source={evaluation},target=/evaluation',
             '--mount',f'type=bind,source={REV/"overlay/request_understanding.py"},target=/app/app/agent/request_understanding.py,readonly',
             '--mount',f'type=bind,source={ROOT/"reports/offline-wheel-cache"},target=/wheels,readonly',
             plan['runtime']['image'],'-c',bootstrap,mode,case,variant]
        if (REV/'overlay/task_loop.py').exists():
            offset=cmd.index(plan['runtime']['image'])
            cmd[offset:offset]=['--mount',f'type=bind,source={REV/"overlay/task_loop.py"},target=/app/app/agent/task_loop.py,readonly']
        result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',timeout=180)
        output=result.stdout+result.stderr
        if key:output=output.replace(key,'<REDACTED>')
        (evaluation/REV.name/('container-'+case+'-'+variant+'.log')).write_text(output,encoding='utf-8')
        for line in result.stdout.splitlines():
            if line.startswith('CASE_RESULT='):print(line)
        if result.returncode and not (evaluation/REV.name/'cases'/ (case if variant=='case' else variant)/'report.json').exists():
            raise RuntimeError('container_failed_before_report')
        print('EVALUATION_ROOT='+str(evaluation))
        return result.returncode
    except BaseException as exc:
        if mode=='live':save(REV/'STOP.json',{'case_id':case,'error':type(exc).__name__})
        raise
    finally:
        found=subprocess.run(['docker','inspect','--format','{{index .Config.Labels "aicshub.eval.owner"}}',container],capture_output=True,text=True)
        if found.returncode==0 and found.stdout.strip()==nonce:
            subprocess.run(['docker','rm','-f',container],capture_output=True,check=True)
        envpath.unlink(missing_ok=True)
        if mode=='live' and lock.exists() and lock.read_text()==nonce:lock.unlink()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['prepare','mock','live'],required=True)
    parser.add_argument('--case',default='BA-05')
    parser.add_argument('--variant',default='case',choices=['case','single-rest','single-ws','multi-rest','multi-ws'])
    args=parser.parse_args()
    if args.mode=='prepare':prepare()
    else:raise SystemExit(run(args.mode,args.case,args.variant))
