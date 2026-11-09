"""Extract current item evidence verbatim; all replies still require final Judge."""
from decimal import Decimal,InvalidOperation
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.state import AgentState


def evidence_reply(state:'AgentState')->str|None:
    from app.agent.intent_items import snapshot,catalog,execution_gaps
    context=snapshot(state)
    if (not context or context.get('scope_valid') is False or execution_gaps(state)
            or any(item['kind'] not in {'knowledge','query_order'} for item in context['items'])):
        return None
    entries=catalog(state);bodies=[]
    for item in context['items']:
        refs=[entries[key] for key in item['evidence_ids'] if key in entries]
        if item['kind']=='knowledge':
            sources=[entry['data']['content'] for entry in refs if entry['kind']=='knowledge']
            if sources:
                if any(not text.strip() or len(text)>1200 or re.search(r'【i\d+】|参考来源[：:]',text) for text in sources):
                    return None
                body='\n'.join(dict.fromkeys(sources))
            elif item['error_code']=='no_item_evidence':
                body='本次检索未取得本项的可用资料，暂时无法确认。'
            else:return None
        elif item['error_code']=='missing_order_id':
            body='当前尚未获得订单号，暂时无法查询订单状态。请提供订单号。'
        else:
            reads=[entry['data'] for entry in refs if entry['kind']=='query_order' and entry['data'].get('success') is True]
            if len(reads)!=1:return None
            data=reads[0].get('data') or {}
            if data.get('order_id')!=state.task.order_id or data.get('status') not in {
                '待付款','待发货','已付款','已发货','已签收','已完成','已取消','运输中'}:
                return None
            body=f"订单{data['order_id']}当前状态为{data['status']}。"
            if data.get('total_amount') is not None:
                try:amount=Decimal(str(data['total_amount']))
                except (InvalidOperation,ValueError):return None
                if not amount.is_finite() or amount<0:return None
                body+=f'订单金额{amount}元。'
        bodies.append(f"【{item['item_id']}】{body}")
    result='\n\n'.join(bodies)
    return result if len(result)<=5000 else None
