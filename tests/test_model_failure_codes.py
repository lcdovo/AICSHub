from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from pydantic import BaseModel
from app.services.model_failure import failure_code
from app.services.llm_service import LLMService,ModelConfig


@pytest.mark.parametrize('exception,expected',[(RuntimeError('daily_model_budget_exhausted'),'daily_model_budget_exhausted'),
    (ValueError('secret payload'),'structured_output_invalid'),(TimeoutError('secret endpoint'),'provider_timeout'),
    (RuntimeError('private database credentials'),'provider_or_internal_error')])
def test_failure_code_never_contains_sensitive_exception_text(exception,expected):
    assert failure_code(exception)==expected


async def test_local_budget_rejection_does_not_trip_provider_circuit_or_retry(monkeypatch):
    class Contract(BaseModel):value:str
    service=LLMService()
    service.models=[ModelConfig('primary',api_key='synthetic',api_base='http://offline.invalid',model='test')]
    monkeypatch.setattr(service,'is_available',lambda:True)
    monkeypatch.setattr(service,'_get_chat_model',lambda *a,**kw:object())
    monkeypatch.setattr(service,'_with_structured_output',lambda *a:object())
    gateway=AsyncMock(side_effect=RuntimeError('daily_model_budget_exhausted'))
    monkeypatch.setattr(service,'_gateway_invoke',gateway)
    breaker=Mock();breaker.can_execute.return_value=True;service.circuit_breaker=breaker
    try:
        assert await service.run_structured('system','synthetic',Contract) is None
        assert service.get_last_call_metadata()['failure_code']=='daily_model_budget_exhausted'
        gateway.assert_awaited_once();breaker.record_failure.assert_not_called()
    finally:await service.close()


async def test_budget_code_survives_understanding_and_gets_precise_user_notice(monkeypatch):
    from app.config.config import Settings,settings
    from scripts.offline_profiles import ACTIVE_CONFIG
    from app.agent.nodes.intent_node import recognize_request
    from app.agent.state import AgentState
    from tests.test_m4_intent_dependencies import dependencies
    config=Settings(_env_file=None,**ACTIVE_CONFIG)
    for key in ACTIVE_CONFIG:monkeypatch.setattr(settings,key,getattr(config,key))
    model=SimpleNamespace(is_available=lambda:True,run_structured=AsyncMock(return_value=None),
                          get_last_call_metadata=lambda:{'failure_code':'daily_model_budget_exhausted'})
    state=AgentState(session_id='budget-test',user_id=101,user_message='退货包装要求是什么？',
                     understanding_mode='model',task_loop_enabled=True)
    # The transport/API layer need not see any sensitive provider exception.
    deps=dependencies(model=model)
    await recognize_request(deps,state)
    assert '今日模型额度不足' in state.reply
    assert state.collected_info['understanding_failure']['code']=='daily_model_budget_exhausted'
