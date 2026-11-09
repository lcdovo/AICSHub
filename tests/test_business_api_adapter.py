"""Smoke-test real isolated API/WS graph wiring without a provider or business acceptance claim."""
from copy import deepcopy

import pytest
from app.config.config import Settings, settings
from scripts.offline_profiles import ACTIVE_CONFIG
from scripts.business_acceptance import load_cases
from scripts.business_api_adapter import api_case, unavailable_model


@pytest.mark.parametrize('transport',['rest','websocket'])
def test_adapter_uses_real_router_graph_and_isolated_session(tmp_path,monkeypatch,transport):
    parsed=Settings(_env_file=None,**ACTIVE_CONFIG)
    for key in ACTIVE_CONFIG:monkeypatch.setattr(settings,key,getattr(parsed,key))
    monkeypatch.setattr(settings,'MCP_ENABLED',False)
    monkeypatch.setattr(settings,'USER_MEMORY_ENABLED',False)
    monkeypatch.setattr(settings,'LANGFUSE_ENABLED',False)
    case=deepcopy(load_cases()[0][0]);case['transport']=transport;case['orders']=[]
    turn={'turn_id':'t1','message':'你好'}
    with api_case(case,tmp_path,load_cases()[1],install_model=unavailable_model) as (send,records):
        row=send(turn)
        assert row['payload']['reply']
        assert row['effects']=={'simulated_applications':0,'preview_count':0}
        replay=send(turn,replay=True)
        assert replay['payload']['run_id']==row['payload']['run_id']
        assert len(records)==2
