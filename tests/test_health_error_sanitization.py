import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from starlette.requests import Request

from app.config.config import settings
from app import main


def test_health_probe_returns_failure_codes_without_backend_exception_text(monkeypatch):
    secret = "mysql://private-user:private-password@db/private"

    class FailedEngine:
        @asynccontextmanager
        async def connect(self):
            raise RuntimeError(secret)
            yield  # pragma: no cover

    class FailedRedis:
        async def ping(self):
            raise RuntimeError(secret)

    monkeypatch.setattr(main, "engine", FailedEngine())
    monkeypatch.setattr(main, "get_redis", lambda: _async_result(FailedRedis()))
    monkeypatch.setattr(settings, "USE_MILVUS", False)
    monkeypatch.setattr(main.app.state, "dependency_health", {}, raising=False)

    result = asyncio.run(main._probe_dependencies())

    assert result["database"]["reason"] == "provider_or_internal_error"
    assert result["redis"]["reason"] == "provider_or_internal_error"
    assert secret not in repr(result)


async def _async_result(value):
    return value


def test_global_exception_response_never_echoes_exception_text(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", True)
    request = Request({"type": "http", "method": "GET", "path": "/synthetic", "headers": []})

    response = asyncio.run(main.global_exception_handler(request, RuntimeError("private database credential")))

    assert response.status_code == 500
    assert b"private database credential" not in response.body
