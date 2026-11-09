"""Real SDK at the original single-WS stream seam, with a fake network only."""
import json
import sqlite3

import httpx
from openai import AsyncOpenAI
import pytest
from scripts.business_http_boundary import BudgetLedger, budget_transport
from scripts.m5_live_budget import OPERATION


@pytest.mark.parametrize('stream',[False,True],ids=['rest-json','websocket-sse'])
async def test_budgeted_sdk_supports_both_reply_protocols(tmp_path,stream):
    events=[];calls=[]
    ledger=BudgetLedger(tmp_path/'dispatch.sqlite','synthetic-fixed',
        {'max_requests':1,'max_input_tokens':24576,'max_output_tokens':2048},
        {'BA-04/t1/http-3':2048},stream_authorized=True)
    def send(request):
        calls.append(request)
        common={'id':'synthetic','object':'chat.completion.chunk','created':1,'model':'synthetic'}
        if stream:
            chunks=[{**common,'choices':[{'index':0,'delta':{'content':'合成步骤。'},'finish_reason':None}]},
                    {**common,'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]},
                    {**common,'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':5}}]
            data=b''.join(('data: '+json.dumps(x,ensure_ascii=False)+'\n\n').encode() for x in chunks)+b'data: [DONE]\n\n'
            class Bytes(httpx.AsyncByteStream):
                def __init__(self):self.parts=iter(data)
                def __aiter__(self):return self
                async def __anext__(self):
                    try:return bytes([next(self.parts)])
                    except StopIteration:raise StopAsyncIteration
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Bytes(),request=request)
        return httpx.Response(200,json={**common,'object':'chat.completion','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'合成步骤。'}}],'usage':{'prompt_tokens':10,'completion_tokens':5}},request=request)
    transport=budget_transport(ledger,model='synthetic',endpoint='https://offline.invalid/v1',underlying=httpx.MockTransport(send),on_event=lambda *a:events.append(a))
    token=OPERATION.set('BA-04/t1/http-3')
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            sdk=AsyncOpenAI(api_key='synthetic-only',base_url='https://offline.invalid/v1',http_client=client,max_retries=0)
            response=await sdk.chat.completions.create(model='synthetic',messages=[{'role':'user','content':'synthetic'}],max_tokens=2048,stream=stream,**({'stream_options':{'include_usage':True}} if stream else {}))
            if stream:
                try: result=''.join(c.choices[0].delta.content or '' for c in [c async for c in response] if c.choices)
                finally:await response.close()
            else:result=response.choices[0].message.content
    finally:OPERATION.reset(token)
    assert result=='合成步骤。' and len(calls)==1
    assert ledger.snapshot()['requests']==1 and ledger.snapshot()['usage_unknown_requests']==0
    assert ledger.snapshot()['known_actual_input_tokens']==10


@pytest.mark.parametrize('fault', ['missing_usage','truncated','length','duplicate_usage','boolean_usage',
    'wrong_choice','malformed_json','timeout','cancel','not_authorized','no_footer_request',
    'oversize','wrong_endpoint','duplicate_operation','json_missing_usage','json_error','double_settle'])
async def test_stream_and_json_failures_never_retry_or_refund(tmp_path,fault):
    events=[];calls=[];closed=[]
    ledger=BudgetLedger(tmp_path/'dispatch.sqlite','synthetic-fixed',
        {'max_requests':2,'max_input_tokens':49152,'max_output_tokens':4096},
        {'BA-04/t1/http-3':2048},stream_authorized=fault!='not_authorized')
    def send(request):
        calls.append(request)
        if fault=='timeout':raise httpx.ReadTimeout('synthetic timeout')
        if fault in {'json_missing_usage','json_error'}:
            return httpx.Response(200 if fault=='json_missing_usage' else 429,json={'choices':[]},request=request)
        common={'id':'synthetic','object':'chat.completion.chunk','created':1,'model':'synthetic'}
        chunks=[{**common,'choices':[{'index':1 if fault=='wrong_choice' else 0,'delta':{'content':'合成步骤。'},'finish_reason':None}]},
                {**common,'choices':[{'index':0,'delta':{},'finish_reason':'length' if fault=='length' else 'stop'}]}]
        usage={**common,'choices':[],'usage':{'prompt_tokens':True if fault=='boolean_usage' else 10,'completion_tokens':5}}
        if fault!='missing_usage':chunks.append(usage)
        if fault=='duplicate_usage':chunks.append(usage)
        frames=[('data: '+json.dumps(x,ensure_ascii=False)+'\n\n').encode() for x in chunks]
        if fault=='malformed_json':frames[0]=b'data: {invalid}\n\n'
        if fault!='truncated':frames.append(b'data: [DONE]\n\n')
        class Bytes(httpx.AsyncByteStream):
            def __init__(self):self.parts=iter(frames)
            def __aiter__(self):return self
            async def __anext__(self):
                try:return next(self.parts)
                except StopIteration:raise StopAsyncIteration
            async def aclose(self):closed.append(True)
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Bytes(),request=request)
    transport=budget_transport(ledger,model='synthetic',endpoint='https://offline.invalid/v1',underlying=httpx.MockTransport(send),on_event=lambda *a:events.append(a))
    token=OPERATION.set('BA-04/t1/http-3');caught=False
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            sdk=AsyncOpenAI(api_key='synthetic-only',base_url='https://offline.invalid/v1',http_client=client,max_retries=0)
            async def once():
                if fault=='wrong_endpoint':
                    await client.post('https://wrong.invalid/v1/chat/completions',json={'model':'synthetic','max_tokens':2048})
                    return
                stream=await sdk.chat.completions.create(model='synthetic',messages=[{'role':'user','content':'x'*25000 if fault=='oversize' else 'synthetic'}],max_tokens=2048,stream=fault not in {'json_missing_usage','json_error'},**({} if fault in {'no_footer_request','json_missing_usage','json_error'} else {'stream_options':{'include_usage':True}}))
                if fault in {'json_missing_usage','json_error'}:return
                try:
                    if fault=='cancel':await stream.__anext__()
                    else:[chunk async for chunk in stream]
                finally:await stream.close()
            try:
                await once()
                if fault=='duplicate_operation':await once()
                if fault=='double_settle':ledger.settle('BA-04/t1/http-3',httpx.Response(200,json={'usage':{'prompt_tokens':999,'completion_tokens':999}}))
            except Exception:caught=True
    finally:OPERATION.reset(token)
    snapshot=ledger.snapshot()
    if fault in {'not_authorized','no_footer_request','oversize','wrong_endpoint'}:
        assert caught and not calls and snapshot['requests']==0
    elif fault=='cancel':
        assert not caught and len(calls)==1 and snapshot['usage_unknown_requests']==1
        assert any(event[0]=='failure' and event[1]['reason']=='stream_abandoned' for event in events)
    elif fault in {'length','duplicate_operation','double_settle'}:
        assert caught and len(calls)==1 and snapshot['requests']==1 and snapshot['usage_unknown_requests']==0
        assert snapshot['known_actual_input_tokens']==10
    else:
        assert caught and len(calls)==1 and snapshot['requests']==1 and snapshot['usage_unknown_requests']==1
    if calls and fault not in {'timeout','json_missing_usage','json_error'}:assert closed


@pytest.mark.parametrize('style',['crlf','multiline','duplicate_json_key'])
async def test_sse_framing_and_json_ambiguity(tmp_path,style):
    from scripts.business_http_boundary import SSEAudit
    ledger=BudgetLedger(tmp_path/'dispatch.sqlite','synthetic',{'max_requests':1,'max_input_tokens':24576,'max_output_tokens':2048},{'op':2048},stream_authorized=True)
    ledger.reserve('op',json.dumps({'model':'synthetic','stream':True,'stream_options':{'include_usage':True},'max_tokens':2048}).encode())
    audit=SSEAudit(ledger,'op',200,lambda *a:None)
    if style=='duplicate_json_key':
        with pytest.raises(ValueError,match='duplicate_json_key'):
            audit.feed(b'data: {"choices":[],"choices":[]}\n\n')
        assert ledger.snapshot()['usage_unknown_requests']==1
        return
    frame={'choices':[{'index':0,'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':5}}
    text=json.dumps(frame,indent=2) if style=='multiline' else json.dumps(frame)
    payload='\n'.join('data: '+line for line in text.splitlines())+'\n\n'+'data: [DONE]\n\n'
    if style=='crlf':payload=payload.replace('\n','\r\n')
    audit.feed(payload.encode());audit.finish()
    assert ledger.snapshot()['usage_unknown_requests']==0


async def test_sdk_stream_handles_http_gzip_without_double_decoding(tmp_path):
    import gzip
    ledger=BudgetLedger(tmp_path/'dispatch.sqlite','compressed',{'max_requests':1,'max_input_tokens':24576,'max_output_tokens':2048},{'op':2048},stream_authorized=True)
    common={'id':'synthetic','object':'chat.completion.chunk','created':1,'model':'synthetic'}
    chunks=[{**common,'choices':[{'index':0,'delta':{'content':'压缩响应。'},'finish_reason':None}]},
            {**common,'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]},
            {**common,'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':5}}]
    raw=b''.join(('data: '+json.dumps(x,ensure_ascii=False)+'\n\n').encode() for x in chunks)+b'data: [DONE]\n\n'
    encoded=gzip.compress(raw)
    class Bytes(httpx.AsyncByteStream):
        def __init__(self):self.parts=iter(encoded)
        def __aiter__(self):return self
        async def __anext__(self):
            try:return bytes([next(self.parts)])
            except StopIteration:raise StopAsyncIteration
    def send(request):return httpx.Response(200,headers={'content-type':'text/event-stream','content-encoding':'gzip','content-length':str(len(encoded))},stream=Bytes(),request=request)
    token=OPERATION.set('op')
    try:
        async with httpx.AsyncClient(transport=budget_transport(ledger,model='synthetic',endpoint='https://offline.invalid/v1',underlying=httpx.MockTransport(send))) as client:
            sdk=AsyncOpenAI(api_key='synthetic-only',base_url='https://offline.invalid/v1',http_client=client,max_retries=0)
            stream=await sdk.chat.completions.create(model='synthetic',messages=[{'role':'user','content':'synthetic'}],max_tokens=2048,stream=True,stream_options={'include_usage':True})
            try:text=''.join(c.choices[0].delta.content or '' for c in [c async for c in stream] if c.choices)
            finally:await stream.close()
    finally:OPERATION.reset(token)
    assert text=='压缩响应。' and ledger.snapshot()['usage_unknown_requests']==0
