from scripts.latency_results import outcome,summarize


def test_timeout_in_policy_is_not_a_degradation():
    row={'http_status':200,'reply':'10分钟内完成支付，超时未支付取消订单。','review':{'passed':True}}
    assert outcome(row)=='review_accepted_not_semantically_verified'


def test_legacy_heuristic_cannot_prove_business_success():
    assert outcome({'http_status':200,'reply':'正常回答','review_accepted':True})=='unclassified'


def test_fast_refusals_never_improve_business_success_tail():
    rows=[{'scenario':'refund','http_status':200,'reply':'本轮请求理解暂时不可用','elapsed_ms':10} for _ in range(99)]
    rows.append({'scenario':'refund','http_status':200,'reply':'已核实','elapsed_ms':5000,'review':{'passed':True},'semantic_review_passed':True})
    result=summarize(rows)['refund']
    assert result['all_response_p95_ms']==10
    assert result['business_verified_p95_ms']==5000 and result['business_verified_n']==1
    assert not result['stable_tail_estimate']


def test_no_review_returns_no_invented_normal_completion_p95():
    result=summarize([{'scenario':'query_order','http_status':200,'reply':'订单回复','elapsed_ms':1}])['query_order']
    assert result['business_verified_p95_ms'] is None
