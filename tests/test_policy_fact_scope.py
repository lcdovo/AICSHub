"""Policy tables and thresholds are not claims about an individual payment/order."""
import pytest
from app.agent.critical_facts import critical_fact_issues

ORDER={'success':True,'data':{'order_id':'ORD950099','status_code':'delivered','total_amount':'0.10','currency':'CNY'}}


@pytest.mark.parametrize('text',[
    '订单ORD950099已签收，订单金额0.10元。\n运费政策：订单金额满99元包邮。',
    '订单ORD950099金额0.10元；活动规则：订单满99元可包邮。',
    '| 退款到账 | 1-7个工作日 | 视支付方式 |',
    '退款到账：1-7个工作日，具体视支付方式。',
    '订单ORD950099已签收，金额0.10元。\n### 4.2 免运费条件\n- 订单金额满99元\n- 会员全场包邮',
])
def test_policy_scope_does_not_invent_individual_contradiction(text):
    assert not critical_fact_issues(text,[ORDER])


@pytest.mark.parametrize('text',[
    '订单ORD950099金额99元。政策：满99元包邮。',
    '政策：满99元包邮，本订单金额99元。',
    '订单ORD950099应付99元。',
    '退款已到账，预计1-7个工作日。',
    '| 退款已到账 | 1-7个工作日 | 视支付方式 |',
    '退款到账：99元。',
    '本订单金额满99元。',
])
def test_policy_context_never_hides_actual_order_or_payment_claim(text):
    assert critical_fact_issues(text,[ORDER])
