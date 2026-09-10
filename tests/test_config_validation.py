"""配置跨字段校验回归测试：before 校验器 + 生产安全校验。

校验器在进程启动即失败（fail fast），避免配置自相矛盾时运行时静默失效。
"""
import pytest

from app.config.config import Settings


def _settings(**overrides) -> Settings:
    # 显式传入测试值并隔离 .env，保证测试确定性。
    return Settings(_env_file=None, **overrides)


def test_chunk_overlap_must_be_less_than_chunk_size():
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        _settings(CHUNK_SIZE=100, CHUNK_OVERLAP=200)


def test_service_port_must_be_in_valid_range():
    with pytest.raises(ValueError, match="REDIS_PORT"):
        _settings(REDIS_PORT=0)


def test_llm_max_tokens_must_not_exceed_daily_budget():
    with pytest.raises(ValueError, match="LLM_MAX_TOKENS"):
        _settings(LLM_MAX_TOKENS=5000, LLM_DAILY_USER_TOKEN_BUDGET=1000)


def test_rag_threshold_must_be_between_zero_and_one():
    with pytest.raises(ValueError, match="RAG_SIMILARITY_THRESHOLD"):
        _settings(RAG_SIMILARITY_THRESHOLD=1.5)


def test_env_string_values_are_tolerated():
    # before 校验发生在字段类型解析前，环境变量多为字符串，必须容忍。
    s = _settings(MYSQL_PORT="3306", CHUNK_SIZE="500", CHUNK_OVERLAP="50")
    assert s.MYSQL_PORT == 3306
    assert s.CHUNK_OVERLAP == 50


def test_production_security_still_enforced():
    with pytest.raises(ValueError, match="AUTH_SECRET_KEY"):
        _settings(ENVIRONMENT="production", AUTH_SECRET_KEY="short-key")