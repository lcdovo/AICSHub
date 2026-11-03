"""真实本机HTTP/SDK/隔离MySQL连接生命周期；协议内容是合成替身，无供应商费用。"""

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from langchain_core.messages import HumanMessage
from app.config.config import settings
from app.models.models import ModelCallRecord, ModelConcurrencySlot
from app.services.llm_service import LLMService, ModelConfig, IntentClassification
from app.services.model_gateway import model_context


@contextmanager
def synthetic_server():
    lock = threading.Lock()
    counters = {"requests": 0, "active_connections": 0, "peak_connections": 0, "accepted_connections": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            with lock:
                counters["accepted_connections"] += 1
                counters["active_connections"] += 1
                counters["peak_connections"] = max(counters["peak_connections"], counters["active_connections"])

        def finish(self):
            try:
                super().finish()
            finally:
                with lock:
                    counters["active_connections"] -= 1

        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            length = int(self.headers["Content-Length"])
            assert 0 < length < 65536
            body = json.loads(self.rfile.read(length))
            assert body["model"] == "synthetic-local-soak"
            with lock:
                counters["requests"] += 1
                number = counters["requests"]
            envelope = {"id": f"synthetic-{number}", "created": 1, "model": body["model"]}
            usage = {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
            if body.get("stream"):
                parts = [
                    {**envelope, "object": "chat.completion.chunk", "choices": [
                        {"index": 0, "delta": {"role": "assistant", "content": "synthetic reply"}, "finish_reason": None}]},
                    {**envelope, "object": "chat.completion.chunk", "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}]},
                    {**envelope, "object": "chat.completion.chunk", "choices": [], "usage": usage},
                ]
                payload = ("".join("data: " + json.dumps(p) + "\n\n" for p in parts) + "data: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                message = {"role": "assistant", "content": "synthetic reply"}
                finish = "stop"
                if body.get("tools"):
                    name = body["tools"][0]["function"]["name"]
                    args = {"reply": "synthetic reply", "intent": "technical"} if name == "LLMResponse" else {"intent": "technical"}
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": f"synthetic-tool-{number}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}]}
                    finish = "tool_calls"
                payload = json.dumps({**envelope, "object": "chat.completion", "usage": usage,
                                      "choices": [{"index": 0, "message": message, "finish_reason": finish}]}).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=False)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", counters
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_local_http_sdk_pool_reuse_close_and_restart_260_calls(real_case, monkeypatch, request):
    url = real_case._repository.engine.url.set(drivername="mysql+aiomysql")
    knowledge_keys = ("EMBEDDING_API_BASE", "EMBEDDING_MODEL", "EMBEDDING_DIM",
                      "RERANKER_LOCAL_BASE", "RERANKER_LOCAL_MODEL")
    knowledge_configuration = {key: getattr(settings, key) for key in knowledge_keys}
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 256)
    monkeypatch.setattr(settings, "LLM_DAILY_USER_TOKEN_BUDGET", 1000000)
    monkeypatch.setattr(settings, "LLM_DAILY_USER_COST_USD", 100)
    monkeypatch.setattr(settings, "LLM_MAX_COST_USD", 1)
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 4)
    monkeypatch.setattr(settings, "LLM_PRICE_VERIFIED", False)
    started = time.monotonic()

    async def exercise(base, counters):
        engine = create_async_engine(url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        closed_clients = 0
        connections_by_mode = {}

        async def cycle(number, count):
            nonlocal closed_clients
            service = LLMService()
            service.models = [ModelConfig("synthetic", api_base=base, api_key="synthetic-local-only",
                                         model="synthetic-local-soak", structured_output_method="function_calling")]
            service.local_model = None
            roots = []
            try:
                for index in range(count):
                    async with model_context(990101, f"local-soak-{number}-{index}", factory=factory):
                        mode = index % 4
                        connections_before = counters["accepted_connections"]
                        if mode == 0:
                            value = await service.run_structured("Return JSON.", "synthetic", IntentClassification,
                                                                 tags=["soak_structured"])
                            assert value and value.intent == "technical"
                        elif mode == 1:
                            reply, _, _ = await service.chat(990101, "synthetic-session", "synthetic")
                            assert reply == "synthetic reply"
                        elif mode == 2:
                            value = "".join([p async for p in service.stream_chat(990101, "synthetic-session", "synthetic")])
                            assert value == "synthetic reply"
                        else:
                            model = service.models[0]
                            response = await service._gateway_invoke(model, [HumanMessage(content="synthetic")],
                                                                     service._get_chat_model(model), "soak_plain")
                            assert response.content == "synthetic reply"
                        connections_by_mode[mode] = connections_by_mode.get(mode, 0) + (
                            counters["accepted_connections"] - connections_before)
                roots = [getattr(model, attr) for model in service._chat_models.values()
                         for attr in ("root_client", "root_async_client")]
            finally:
                await service.close()
            assert not service._chat_models
            for client in roots:
                assert client.is_closed()
            closed_clients += len({id(c) for c in roots})
            for _ in range(100):
                if counters["active_connections"] == 0:
                    break
                await asyncio.sleep(.01)
            assert counters["active_connections"] == 0

        try:
            await cycle(0, 200)  # 长实例复用四种调用接口与连接池。
            reused_connections = counters["accepted_connections"]
            initial_mode_connections = dict(connections_by_mode)
            # 当前OpenAI SDK在[DONE]后主动关闭响应，SSE可使下一普通请求重连。
            # 累计TCP数不是泄漏指标；另外实测连续普通调用确实复用，所有模式关闭后归零。
            before_plain = counters["accepted_connections"]
            service = LLMService()
            service.models = [ModelConfig("synthetic", api_base=base, api_key="synthetic-local-only",
                                         model="synthetic-local-soak", structured_output_method="function_calling")]
            plain_roots = []
            try:
                for index in range(20):
                    async with model_context(990101, f"plain-pool-{index}", factory=factory):
                        value = await service.run_structured("Return JSON", "synthetic", IntentClassification)
                        assert value and value.intent == "technical"
                plain_roots = [getattr(model, attr) for model in service._chat_models.values()
                               for attr in ("root_client", "root_async_client")]
            finally:
                await service.close()
            assert all(client.is_closed() for client in plain_roots)
            closed_clients += len({id(client) for client in plain_roots})
            plain_connections = counters["accepted_connections"] - before_plain
            assert plain_connections == 1
            for _ in range(100):
                if counters["active_connections"] == 0:
                    break
                await asyncio.sleep(.01)
            assert counters["active_connections"] == 0
            for index in range(1, 11):
                await cycle(index, 4)  # 模拟服务重建与关闭，不复用已关闭客户端。
            async with factory() as db:
                rows = (await db.scalars(select(ModelCallRecord))).all()
                assert len(rows) == 260 and all(r.status == "succeeded" and r.user_id == 990101 for r in rows)
                assert all(r.input_tokens == 2 and r.output_tokens == 1 for r in rows)
                assert await db.scalar(select(func.count()).select_from(ModelConcurrencySlot).where(
                    ModelConcurrencySlot.call_id.is_not(None))) == 0
            assert counters["requests"] == 260 and counters["active_connections"] == 0
            assert counters["peak_connections"] <= 4
            return {**counters, "closed_clients": closed_clients, "mixed_cycle_connections": reused_connections,
                    "continuous_plain_connections": plain_connections,
                    "initial_connections_by_mode": initial_mode_connections,
                    "all_connections_by_mode": connections_by_mode,
                    "service_lifecycles": 12}
        finally:
            await engine.dispose()

    with synthetic_server() as (base, counters):
        stats = asyncio.run(exercise(base, counters))
    assert knowledge_configuration == {key: getattr(settings, key) for key in knowledge_keys}
    report = {"status": "PASS", "scope": "real local HTTP/SDK/MySQL; synthetic provider, no external calls",
              "duration_seconds": round(time.monotonic() - started, 3), **stats,
              "embedding_reranker_config_preserved": True,
              "not_proven": "multi-hour leak soak and real supplier sustained load"}
    output = Path(request.config.option.xmlpath).parent / "local-http-soak.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
