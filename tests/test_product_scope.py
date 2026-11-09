from app.services.knowledge_base.product_scope import scope_candidates
from tests.test_m3_lifecycle import kb,execute


def test_printer_procedure_cannot_use_payment_or_account_sources():
    rows=[{'id':'pay','title':'支付安全','content':'支付异常请重置密码'},
          {'id':'ear','title':'耳机说明','content':'配对重置步骤'},
          {'id':'printer','title':'打印机指南','content':'卡纸请先断电'}]
    selected,meta=scope_candidates('打印机卡纸怎么排查？',rows)
    assert [i['id'] for i in selected]==['printer'] and meta['input_count']==3


def test_missing_device_is_explicit_limit_not_global_absence():
    selected,meta=scope_candidates('打印机卡纸处理',[{'id':'account','title':'账号安全','content':'修改密码'}])
    assert selected==[] and meta['reason']=='device_not_in_retrieved_sources'


def test_generic_warranty_and_multi_device_queries_are_not_lexically_pruned():
    rows=[{'id':'policy','title':'平台保修政策','content':'电子产品享受保修'}]
    assert scope_candidates('打印机保修多久？',rows)[0]==rows
    assert scope_candidates('打印机和耳机故障怎么处理？',rows)[0]==rows


def test_actual_sql_retrieval_and_cache_do_not_publish_wrong_device(kb,monkeypatch):
    from app.config.config import settings
    monkeypatch.setattr(settings,'RAG_USE_RERANKER',False)
    for title,content in [('支付安全','设备卡纸异常请查看支付记录。'),('耳机指南','卡纸故障、重置与重新配对。')]:
        execute(kb,kb.add_document(title,content))
    first=kb.search('打印机卡纸应该怎么排查？')
    assert first['results']==[] and first['evidence_bundle']['items']==[]
    assert first['meta']['product_scope']['reason']=='device_not_in_retrieved_sources'
    cached=kb.search('打印机卡纸应该怎么排查？')
    assert cached['from_cache'] and not cached['results']
    execute(kb,kb.add_document('打印机故障说明','打印机卡纸处理前请先断电，参考型号说明书。'))
    updated=kb.search('打印机卡纸应该怎么排查？')
    assert updated['results'] and all('打印机' in x['title']+x['content'] for x in updated['results'])
