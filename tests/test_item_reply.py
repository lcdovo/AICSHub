"""Item identity and markup are server owned; model content still requires Judge."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import httpx
from openai import AsyncOpenAI
import pytest

from app.agent.item_reply import ITEM_REPLY_IDS,reply_contract,render_reply
from app.services.llm_service import LLMResponse,LLMService,ModelConfig
from app.services.model_gateway import model_context
from app.config.config import settings
from tests.test_m4_gateway import factory as factory


def test_reply_slots_own_all_labels_and_reject_missing_or_extra_items():
    schema=reply_contract(LLMResponse,('i1','i2'))
    assert 'reply' not in schema.model_json_schema()['properties']
    value=schema.model_validate({'item_replies':{'i1':'订单已签收。','i2':'退货需保留原包装。'}})
    assert render_reply(value,('i1','i2'))=='【i1】订单已签收。\n\n【i2】退货需保留原包装。'
    for rows in [{'i1':'缺项'},{'i1':'一','i2':'二','i3':'额外'},{'i1':'一','i2':3}]:
        with pytest.raises(ValueError):schema.model_validate({'item_replies':rows})
    with pytest.raises(ValueError):schema.model_validate({'reply':'自由标题'})
    with pytest.raises(ValueError):render_reply(schema.model_validate({'item_replies':{'i1':'【i2】混入','i2':'二'}}),('i1','i2'))


def test_semicolon_clauses_keep_all_text_but_receive_separate_review_slots():
    from app.agent.judge_contracts import response_segments
    schema=reply_contract(LLMResponse,('i1','i2'))
    value=schema.model_validate({'item_replies':{'i1':'质量问题运费由商家承担；其他情形暂无法确认。','i2':'退货需保留原包装。'}})
    reply=render_reply(value,('i1','i2'))
    assert reply=='【i1】质量问题运费由商家承担；\n其他情形暂无法确认。\n\n【i2】退货需保留原包装。'
    segments=response_segments(reply)
    assert [row['text'] for row in segments]==['【i1】质量问题运费由商家承担；','其他情形暂无法确认。','【i2】退货需保留原包装。']
    assert ''.join(row['text'] for row in segments)==reply.replace('\n','')


@pytest.mark.parametrize('valid',[True,False])
async def test_actual_sdk_item_reply_is_required_and_rendered_before_review(factory,monkeypatch,valid):
    monkeypatch.setattr(settings,'LLM_DAILY_USER_TOKEN_BUDGET',1000000)
    service=LLMService();service.RETRY_DELAY=0;service.local_model=None
    service.models=[ModelConfig('synthetic',api_key='synthetic',api_base='https://offline.invalid/v1',
        model='synthetic-reply',thinking_mode='disabled',structured_output_method='function_calling')]
    calls=[]
    def respond(request):
        body=json.loads(request.content);calls.append(body)
        tool=body['tools'][0]['function']
        assert tool['name']=='LLMResponse' and 'item_replies' in tool['parameters']['properties']
        assert 'reply' not in tool['parameters']['properties']
        instructions='\n'.join(str(message.get('content','')) for message in body['messages'])
        assert 'item_replies固定字段' in instructions
        value={'item_replies':{'i1':'订单已签收。','i2':'退货需保留原包装。'}} if valid else {'reply':'【订单】错误旧格式'}
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':1,'model':'synthetic-reply',
            'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                'tool_calls':[{'id':'synthetic','type':'function','function':{'name':'LLMResponse','arguments':json.dumps(value,ensure_ascii=False)}}]}}],
            'usage':{'prompt_tokens':20,'completion_tokens':30,'total_tokens':50}},request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider:
        original=service._get_chat_model
        def model(config,**kwargs):
            value=original(config,**kwargs)
            sdk=AsyncOpenAI(api_key=config.api_key,base_url=config.api_base,http_client=provider,max_retries=0)
            value.root_async_client,value.async_client=sdk,sdk.chat.completions
            return value
        token=ITEM_REPLY_IDS.set(('i1','i2'))
        try:
            with patch.object(service,'_get_chat_model',model):
                async with model_context(101,'fixed-item-reply',factory=factory):
                    reply,_,_=await service.chat(user_id=101,session_id='fixed-item-reply',message='合成只读问题')
                    metadata=service.get_last_call_metadata()
        finally: ITEM_REPLY_IDS.reset(token);await service.close()
    assert len(calls)==1
    if valid:
        assert reply=='【i1】订单已签收。\n\n【i2】退货需保留原包装。'
        assert metadata['structured_output']['reply']==reply
    else: assert metadata['degraded']
    assert ITEM_REPLY_IDS.get()==()


async def test_item_websocket_generation_uses_buffered_structured_path_and_resets_context(monkeypatch):
    import asyncio
    from app.agent.nodes.response_node import generate_response_with_llm
    from app.agent.stream_context import stream_event_queue
    from tests.test_intent_items import current
    state=current()
    async def invoke(payload):
        assert ITEM_REPLY_IDS.get()==('i1','i2','i3')
        return '【i1】一\n【i2】二\n【i3】三','query_order',10,{'tokens':10}
    model=SimpleNamespace(stream_chat=AsyncMock(side_effect=AssertionError('must buffer structured items')))
    context=SimpleNamespace(_build_llm_prompt=lambda state:'合成要求',_build_context_messages=lambda state:[],
        llm_service=model,llm_runnable=SimpleNamespace(ainvoke=invoke))
    token=stream_event_queue.set(asyncio.Queue())
    try:
        assert await generate_response_with_llm(context,state)=='【i1】一\n【i2】二\n【i3】三'
        assert ITEM_REPLY_IDS.get()==()
    finally:stream_event_queue.reset(token)
