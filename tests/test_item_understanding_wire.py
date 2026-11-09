"""One authoritative item list; provider plans cannot compete with server reads."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.agent.request_understanding import understand
from app.config.config import Settings,settings
from scripts.offline_profiles import ACTIVE_CONFIG
from tests.test_refund_read_plan_scope import payload,state


@pytest.fixture(autouse=True)
def active(monkeypatch):
    config=Settings(_env_file=None,**ACTIVE_CONFIG)
    for key in ACTIVE_CONFIG:monkeypatch.setattr(settings,key,getattr(config,key))


async def test_item_provider_schema_omits_redundant_read_plan_and_task_kind():
    value=payload();value.pop('read_tasks')
    model=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=value))
    result=await understand(model,state())
    assert result is not None and result.read_tasks==[] and result.task_kind=='after_sales'
    properties=model.run_structured.call_args.args[2].model_json_schema()['properties']
    assert 'read_tasks' not in properties and 'task_kind' not in properties
    model.run_structured.assert_awaited_once()


@pytest.mark.parametrize('field',['read_tasks','task_kind'])
async def test_old_provider_fields_are_rejected_without_silent_deletion_or_retry(field):
    value=payload()
    if field=='task_kind':value.pop('read_tasks');value['task_kind']='single'
    model=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=value))
    assert await understand(model,state()) is None
    assert field in value
    model.run_structured.assert_awaited_once()


async def test_explicit_reads_are_derived_from_current_validated_items():
    value=payload();value.pop('read_tasks');value.update(topic='query_order',request_type='query',parameters={})
    message='查询ORD980105的状态，并回看我的聊天历史。'
    value['items']=[{'text':'查询ORD980105的状态','kind':'query_order'},
                    {'text':'回看我的聊天历史','kind':'get_user_history'}]
    value['subrequests']=[]
    model=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=value))
    result=await understand(model,state(message))
    assert result is not None
    assert [(x.tool,x.resource_id) for x in result.read_tasks]==[('query_order','ORD980105'),('get_user_history',None)]
