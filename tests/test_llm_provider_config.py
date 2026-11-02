from app.config.config import settings
from unittest.mock import Mock

from app.services.llm_service import IntentClassification, LLMService, ModelConfig


def test_primary_provider_applies_configured_thinking_mode(monkeypatch):
    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(settings, "LLM_API_BASE", "https://token.uino.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-token")
    monkeypatch.setattr(settings, "LLM_MODEL", "deepseek-flash")
    monkeypatch.setattr(settings, "LLM_MODEL_VERSION", "deepseek-flash")
    monkeypatch.setattr(settings, "LLM_THINKING_MODE", "disabled")
    monkeypatch.setattr(settings, "LLM_STRUCTURED_OUTPUT_METHOD", "function_calling")
    monkeypatch.setattr(settings, "LLM_BACKUP_BASE", "")
    monkeypatch.setattr(settings, "LLM_BACKUP_MODEL", "")
    monkeypatch.setattr(settings, "LLM_LOCAL_BASE", "")
    monkeypatch.setattr(settings, "LLM_LOCAL_MODEL", "")
    monkeypatch.setattr("app.services.llm_service.ChatOpenAI", fake_chat_openai)

    service = LLMService()
    service._get_chat_model(service.models[0])

    assert service.models[0].model == "deepseek-flash"
    assert service.models[0].api_base == "https://token.uino.com/v1"
    assert service.local_model is None
    assert service._analytic_models(prefer_local=True) == service.models
    assert service.models[0].structured_output_method == "function_calling"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_unconfigured_thinking_mode_is_not_sent(monkeypatch):
    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(settings, "LLM_API_BASE", "https://example.invalid/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-token")
    monkeypatch.setattr(settings, "LLM_MODEL", "test-model")
    monkeypatch.setattr(settings, "LLM_THINKING_MODE", "")
    monkeypatch.setattr(settings, "LLM_BACKUP_BASE", "")
    monkeypatch.setattr(settings, "LLM_BACKUP_MODEL", "")
    monkeypatch.setattr(settings, "LLM_LOCAL_BASE", "")
    monkeypatch.setattr(settings, "LLM_LOCAL_MODEL", "")
    monkeypatch.setattr("app.services.llm_service.ChatOpenAI", fake_chat_openai)

    service = LLMService()
    service._get_chat_model(service.models[0])

    assert captured["extra_body"] is None


def test_provider_structured_output_uses_function_calling():
    chat = Mock()
    model = ModelConfig(name="primary", structured_output_method="function_calling")
    LLMService._with_structured_output(chat, IntentClassification, model)
    chat.with_structured_output.assert_called_once_with(IntentClassification, method="function_calling")


def test_other_providers_keep_default_structured_output():
    chat = Mock()
    model = ModelConfig(name="backup")
    LLMService._with_structured_output(chat, IntentClassification, model)
    chat.with_structured_output.assert_called_once_with(IntentClassification)
