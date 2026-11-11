"""Isolated real REST/WS routers + graph + SQL, with an explicitly injected model boundary.

This adapter never selects credentials or opens a live provider transport. A live
caller must supply a separately authorized, ledger-guarded model installer.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


@contextmanager
def api_case(case, directory, sources, *, install_model):
    import pytest
    from sqlalchemy import func, select
    from langgraph.checkpoint.memory import InMemorySaver
    from app.agent.graph import AgentGraph
    from app.models.models import Order, RefundAction, RefundApplication
    from app.security import create_access_token
    from tests.test_session_creation import session_api
    from tests.test_chat_auth import receive_turn
    import app.routers.chat as chat

    patch = pytest.MonkeyPatch()
    generator = None
    client = None
    graph = None
    try:
        generator = session_api.__wrapped__(directory, patch)
        client, factory, manager, redis, users = next(generator)
        # Match production lifespan: load MCP dependencies before per-tool deadlines.
        from app.agent.mcp_client import warmup_mcp_client
        client.portal.call(warmup_mcp_client)
        selected = [dict(row) for row in sources['items'] if row['id'] in case['source_ids']]
        class FrozenKnowledge:
            def evidence_is_current(self, items):
                return case.get('fault') != 'publication_epoch_change'
            async def async_search(self, query, top_k):
                if case.get('fault') == 'retrieval_unavailable':
                    raise RuntimeError('synthetic_retrieval_fault')
                return {'success':True,'results':selected,'meta':{'score_type':'rrf','knowledge_epoch':sources['epoch']},
                        'evidence_bundle':{'schema_version':1,'items':selected,'knowledge_epoch':sources['epoch']}}
        patch.setattr('app.agent.graph.get_knowledge_base', lambda:FrozenKnowledge())
        graph = AgentGraph()
        graph.checkpointer = InMemorySaver()
        graph.langgraph = graph._build_langgraph()
        install_model(graph.llm_service, patch)
        if case.get('fault') == 'judge_invalid_json':
            # Emulate strict parser returning no verdict. This is injected failure,
            # not a paid provider response or evidence of provider JSON quality.
            from app.agent.validation_llm import LLMRAGJudge
            graph.llm_judge = LLMRAGJudge(SimpleNamespace(is_available=lambda:True,
                run_structured=AsyncMock(return_value=None)))
        patch.setattr(chat.chat_service, 'process', graph.run)
        patch.setattr(chat.chat_service, 'process_stream', graph.run_stream)
        async def seed():
            async with factory() as db:
                for order in case['orders']:
                    db.add(Order(**order,items=[],received_at=datetime.utcnow()-timedelta(days=1)))
                await db.commit()
        asyncio.run(seed())
        response = client.post('/api/v1/chat/sessions?user_id=101')
        response.raise_for_status()
        sid = response.json()['data']['id']
        records=[]
        def send(turn, *, replay=False):
            if case.get('category')=='websocket_recovery' and turn['turn_id']=='t2' and not replay:
                manager._state_cache.clear()
                redis.values.clear()
            body={'user_id':101,'session_id':sid,'message':turn['message'],
                  'client_message_id':case['case_id']+'-'+turn['turn_id']}
            if case['transport']=='rest':
                result=client.post('/api/v1/chat/send',json=body)
                result.raise_for_status()
                payload=result.json()
            else:
                token=create_access_token(users[0])
                with client.websocket_connect('/api/v1/chat/stream?access_token='+token) as ws:
                    ws.send_json(body)
                    events=receive_turn(ws)
                payload=next(e for e in events if e['type']=='done')
            async def inspect():
                async with factory() as db:
                    return {'simulated_applications':await db.scalar(select(func.count()).select_from(RefundApplication)),
                            'preview_count':await db.scalar(select(func.count()).select_from(RefundAction))}
            effects=asyncio.run(inspect())
            task_id=payload.get('task_id')
            task=client.get('/api/v1/chat/tasks/'+task_id).json().get('data') if task_id else None
            row={'turn_id':turn['turn_id'],'transport':case['transport'],'payload':payload,
                 'task':task,'effects':effects,'replay':replay,'session_id':sid}
            records.append(row)
            return row
        yield send, records
    finally:
        if graph is not None and client is not None and client.portal is not None:
            from app.agent.mcp_client import shutdown_mcp_client
            client.portal.call(shutdown_mcp_client)
            client.portal.call(graph.llm_service.close)
        if generator is not None:
            generator.close()
        patch.undo()


def unavailable_model(service, patch):
    """Offline adapter preflight only: no SDK or provider requests can be dispatched."""
    service.models=[]
    service.local_model=None
