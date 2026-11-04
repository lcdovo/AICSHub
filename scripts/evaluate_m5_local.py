"""有界本地检索开发消融：隔离SQLite/Milvus、新collection、仅loopback模型；不调用聊天/Judge。"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


@contextmanager
def isolated_directory():
    previous=Path.cwd()
    with tempfile.TemporaryDirectory(prefix='aicshub-m5-local-') as temporary:
        try:
            yield temporary
        finally:
            os.chdir(previous)


def verify_index_resource(root: Path) -> tuple[int, str]:
    state=json.loads((root/'reports/m3-closeout/runtime/state.json').read_bytes())
    if not state['prefix'].startswith('aicshub-m3-acceptance-'):
        raise RuntimeError('wrong test prefix')
    cid=state['containers']['milvus']
    raw=subprocess.check_output(['docker','inspect','--format',
        '{{index .Config.Labels "aicshub.test.owner"}}|{{.State.Status}}|{{json .NetworkSettings.Ports}}',cid],text=True).strip()
    owner,status,bindings=raw.split('|',2)
    port=int(state['ports']['milvus'])
    if owner!='m3-closeout' or status!='running' or not any(
        p['HostIp']=='127.0.0.1' and p['HostPort']==str(port) for p in json.loads(bindings).get('19530/tcp',[])):
        raise RuntimeError('unverified test resource')
    return port,cid


def main():
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=12,choices=range(1,13))
    parser.add_argument('--modes',nargs='+',choices=['bm25','dense','hybrid','rerank'],default=['bm25','dense','hybrid','rerank'])
    parser.add_argument('--provider-timeout',type=float,default=5.0)
    args=parser.parse_args()
    if not 0 < args.provider_timeout <= 30:
        parser.error('provider-timeout must be >0 and <=30; business default is5')
    output=root/'reports/m5'/datetime.now().strftime('local-%Y%m%d-%H%M%S')
    output.mkdir(parents=True)
    from scripts.test_provenance import snapshot_sources,compare_sources
    start=snapshot_sources(root)
    (output/'source-start.json').write_text(json.dumps(start,indent=2),encoding='utf-8')
    report={'scope':'37 assistant draft development fixtures; not independent acceptance',
            'external_model_calls':0,'chat_model_calls':0,'whole_m5_pass':False,'status':'INCOMPLETE'}
    old=Path.cwd()
    backend=None; repo=None; index=None
    try:
        port,cid=verify_index_resource(root)
        report['verified_test_owner']='m3-closeout'; report['index_resource']=cid
        tree=ast.parse((root/'app/config/config.py').read_text(encoding='utf-8-sig'))
        for cls in tree.body:
            if isinstance(cls,ast.ClassDef) and cls.name=='Settings':
                for field in cls.body:
                    if isinstance(field,ast.AnnAssign) and isinstance(field.target,ast.Name):
                        os.environ.pop(field.target.id,None)
        with isolated_directory() as temporary:
            os.chdir(temporary)
            os.environ.update(REDIS_ENABLED='false',USE_MILVUS='true',LANGFUSE_ENABLED='false',
                LLM_API_KEY='',LLM_API_BASE='http://offline.invalid/v1',USER_MEMORY_ENABLED='false',
                EMBEDDING_API_BASE='http://127.0.0.1:8012/v1',EMBEDDING_API_KEY='local-only',
                EMBEDDING_MODEL='qwen3-embedding:0.6b',EMBEDDING_DIM='1024',EMBEDDING_REVISION='local-live-probe',
                RERANKER_API_BASE='',RERANKER_API_KEY='',RERANKER_LOCAL_BASE='http://127.0.0.1:7997',
                RERANKER_LOCAL_MODEL='BAAI/bge-reranker-large',RAG_USE_RERANKER='false')
            import httpx
            with httpx.Client(timeout=5,trust_env=False) as client:
                tags=client.get('http://127.0.0.1:8012/api/tags').raise_for_status().json()
                model=next(m for m in tags['models'] if m['name']=='qwen3-embedding:0.6b')
                revision=model['digest']
                models=client.get('http://127.0.0.1:7997/models').raise_for_status().json()
                if not any(m['id']=='BAAI/bge-reranker-large' for m in models['data']):
                    raise RuntimeError('local reranker model mismatch')
            from app.config.config import settings
            settings.EMBEDDING_REVISION='ollama-'+revision[:12]
            from app.models.models import Base,BackgroundJob
            from app.repositories.knowledge_repository import KnowledgeRepository
            from app.services.embedding_service import APIEmbedding
            from app.services.knowledge_base import KnowledgeBaseService
            from app.services.knowledge_base import versioned_search
            # 默认复验业务5秒预算；较长实验必须显式传参，报告与业务结果分开。
            versioned_search.ENCODE_TIMEOUT_SECONDS=args.provider_timeout
            report['experimental_provider_timeout_seconds']=args.provider_timeout
            report['business_default_provider_timeout_seconds']=5
            from app.services.knowledge_base.index_worker import index_job
            from app.services.knowledge_base.vector_index import VersionedVectorIndex
            from scripts.evaluate_m5 import Dataset,ranking_metrics,Chunk,RelevantChunk
            backend=APIEmbedding('http://127.0.0.1:8012/v1','local-only','qwen3-embedding:0.6b',
                                 dim=1024,max_retries=0,timeout=60,batch_size=20)
            class Embedding:
                _dim=1024
                _embedding=backend
                backend_name=backend.name
                calls=0
                def encode_batch(self,texts):
                    self.calls+=1
                    return backend.encode_batch(texts)
                def encode_query(self,text):
                    self.calls+=1
                    return backend.encode_query(text)
            embedding=Embedding()
            repo=KnowledgeRepository('sqlite:///'+(Path(temporary)/'m5.db').as_posix())
            Base.metadata.create_all(repo.engine)
            index=VersionedVectorIndex('127.0.0.1',port,prefix='m5_dev_'+uuid.uuid4().hex[:12])
            kb=KnowledgeBaseService(repository=repo,embedding=embedding,vector_store=index)
            kb.initialize()
            corpus=json.loads((root/'acceptance/m5/corpus.json').read_bytes())
            raw=(root/'acceptance/m5/development.json').read_bytes()
            dataset=Dataset.model_validate(json.loads(raw))
            mapping={}
            for doc in corpus:
                result=kb.add_document(doc['title'],doc['content'])
                with repo.transaction() as db:
                    job=db.get(BackgroundJob,result['job_id']);job.status='running';job.lease_token='m5-local'
                    job.lease_expires_at=datetime.utcnow()+timedelta(seconds=120);job.attempts+=1
                index_job(repo,embedding,index,result['job_id'],'m5-local')
                details=kb.document_detail(result['document_id'])
                if details['status']!='published': raise RuntimeError('local index not published')
                _,chunks=repo.snapshot()
                current=[c for c in chunks if c['version_id']==result['version_id']]
                if len(current)!=1: raise RuntimeError('development label mapping requires exactly one chunk')
                mapping[(doc['chunk_id'],doc['version_id'])]=(current[0]['id'],current[0]['version_id'])
            report.update(embedding_model=backend.model,embedding_revision=settings.EMBEDDING_REVISION,
                reranker_model='BAAI/bge-reranker-large',dataset_sha256=hashlib.sha256(raw).hexdigest(),
                chunks=len(corpus),corpus_sha256=hashlib.sha256((root/'acceptance/m5/corpus.json').read_bytes()).hexdigest())
            report['canonical_to_runtime_labels']=[{'canonical_chunk_id':key[0],'canonical_version_id':key[1],
                'runtime_chunk_id':value[0],'runtime_version_id':value[1]} for key,value in mapping.items()]
            rows=[]
            for mode in args.modes:
                settings.RAG_USE_RERANKER=mode=='rerank'
                for case in [c for c in dataset.cases if c.kind=='answerable'][:args.limit]:
                    # 冷次清本测试实例缓存；不操作业务Redis或业务缓存。
                    kb._query_cache._cache.clear() if hasattr(kb._query_cache,'_cache') else None
                    backend._text_cache.clear()
                    relevant=[RelevantChunk(chunk_id=mapping[c.key()][0],version_id=mapping[c.key()][1],grade=c.grade) for c in case.relevant]
                    for temperature in ['cold','hot']:
                        began=time.perf_counter()
                        result=kb.search(case.query,top_k=12,recall_k=20,rerank_k=20,
                                         retrieval_mode=mode if mode in {'bm25','dense'} else 'hybrid')
                        metrics=ranking_metrics(relevant,[Chunk(chunk_id=c['id'],version_id=c['version_id']) for c in result['results']])
                        rows.append({'case_id':case.case_id,'mode':mode,'cache':temperature,
                            'latency_ms':(time.perf_counter()-began)*1000,'metrics':metrics,
                            'metadata':result['meta'],'ranked_chunks':[{'id':c['id'],'version_id':c['version_id']} for c in result['results']]})
            from statistics import mean
            summaries=[]
            for mode in args.modes:
                for temperature in ['cold','hot']:
                    group=[r for r in rows if r['mode']==mode and r['cache']==temperature]
                    summaries.append({'mode':mode,'cache':temperature,'count':len(group),
                        **{k:mean(r['metrics'][k] for r in group) for k in ['recall_at_20','ndcg_at_5','evidence_hit_at_20']}})
            actual_rerank=all(r['metadata']['reranker']=='local' for r in rows if r['mode']=='rerank')
            report.update(status='PASS_LOCAL_DEVELOPMENT_ONLY' if actual_rerank else 'FAIL_LOCAL_RERANK_FALLBACK',summaries=summaries,rows=rows,
                          embedding_method_calls=embedding.calls,
                          rewrite_status='NOT_RUN_REQUIRES_REWRITE_MODEL_OR_SEPARATELY_FROZEN_RULE_QUERIES',
                          quality_status='NOT_ACCEPTED_INDEPENDENT_HOLDOUT_AND_OUTPUT_LABELS_MISSING')
            backend.close();backend=None;repo.engine.dispose();repo=None;index.client.close();index=None
    except Exception as exc:
        report.update(status='FAIL',error_type=type(exc).__name__)
    finally:
        if backend: backend.close()
        if repo: repo.engine.dispose()
        if index: index.client.close()
        os.chdir(old)
        end=snapshot_sources(root)
        report.update(compare_sources(start,end))
        if not report['source_tree_stable'] or not report['source_snapshot_complete']:
            report['status']='FAIL_SOURCE_DRIFT'
        (output/'source-end.json').write_text(json.dumps(end,indent=2),encoding='utf-8')
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'report':str(output/'report.json'),'error_type':report.get('error_type')},ensure_ascii=False))
    return 0 if report['status']=='PASS_LOCAL_DEVELOPMENT_ONLY' else 2


if __name__=='__main__':
    raise SystemExit(main())
