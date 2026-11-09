"""Evaluation-only JSON/SSE budget boundary: one durable reservation per HTTP attempt."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
from typing import Callable

import httpx
from scripts.m5_live_budget import DispatchLedger, OPERATION


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def strict_json(value):
    return json.loads(value, object_pairs_hook=unique_object)


def valid_usage(value):
    return (isinstance(value, dict) and type(value.get("prompt_tokens")) is int
            and type(value.get("completion_tokens")) is int
            and min(value["prompt_tokens"], value["completion_tokens"]) >= 0)


class BudgetLedger(DispatchLedger):
    def __init__(self, *args, stream_authorized=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.stream_authorized = stream_authorized

    def reserve(self, operation, body):
        payload = strict_json(body)
        stream = payload.get("stream", False)
        if type(stream) is not bool:
            raise ValueError("stream_flag_must_be_boolean")
        if ("max_completion_tokens" in payload and "max_tokens" in payload
                and payload["max_completion_tokens"] != payload["max_tokens"]):
            raise ValueError("ambiguous_output_limit")
        if not stream:
            return super().reserve(operation, body)
        if not self.stream_authorized:
            raise ValueError("stream_requires_explicit_continuation_contract")
        if payload.get("stream_options", {}).get("include_usage") is not True:
            raise ValueError("stream_usage_footer_must_be_requested")
        output = payload.get("max_completion_tokens", payload.get("max_tokens"))
        if (operation not in self.operations or type(output) is not int
                or not 0 < output <= self.operations[operation] or len(body) > 24576):
            raise ValueError("request_outside_operation_budget")
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM dispatches WHERE operation=?", (operation,)).fetchone():
                raise ValueError("operation_already_dispatched_no_retry")
            count, inp, out = db.execute("SELECT count(*),coalesce(sum(input_reserved),0),coalesce(sum(output_reserved),0) FROM dispatches").fetchone()
            if (count + 1 > self.limits["max_requests"] or inp + len(body) > self.limits["max_input_tokens"]
                    or out + output > self.limits["max_output_tokens"]):
                raise ValueError("authorized_budget_exhausted")
            db.execute("INSERT INTO dispatches VALUES (?,?,?,?,?,NULL,NULL,NULL)",
                       (operation, len(body), output, hashlib.sha256(body).hexdigest(), "unknown_dispatched"))

    def settle(self, operation, response):
        try:
            usage = response.json().get("usage")
        except Exception:
            usage = None
        known = valid_usage(usage)
        with closing(sqlite3.connect(self.path)) as db, db:
            result = db.execute("UPDATE dispatches SET status=?,input_actual=?,output_actual=?,http_status=? WHERE operation=? AND status='unknown_dispatched'",
                ("http_complete" if known else "http_complete_usage_unknown",
                 usage["prompt_tokens"] if known else None, usage["completion_tokens"] if known else None,
                 response.status_code, operation))
            if result.rowcount != 1:
                raise ValueError("settlement_without_new_unsettled_reservation")


class SSEAudit:
    def __init__(self, ledger, operation, status, notify):
        self.ledger, self.operation, self.status, self.notify = ledger, operation, status, notify
        self.pending = b""
        self.lines = []
        self.frame_bytes = self.total = 0
        self.done = self.closed = False
        self.usage = self.finish_reason = None

    def event(self):
        if not self.lines:
            return
        data = b"\n".join(self.lines)
        self.lines = []
        self.frame_bytes = 0
        if self.done:
            raise ValueError("stream_data_after_done")
        if data == b"[DONE]":
            self.done = True
            self.ledger.settle(self.operation, httpx.Response(self.status, json={"usage": self.usage}))
            if self.usage is None:
                raise ValueError("stream_usage_missing")
            if self.finish_reason != "stop":
                raise ValueError("stream_finish_not_stop")
            self.notify("settled", {"operation": self.operation, "usage": self.usage})
            return
        value = strict_json(data)
        if not isinstance(value, dict) or not isinstance(value.get("choices"), list):
            raise ValueError("stream_chunk_invalid")
        for choice in value["choices"]:
            if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0:
                raise ValueError("unexpected_stream_choice")
            reason = choice.get("finish_reason")
            if reason is not None:
                if self.finish_reason is not None:
                    raise ValueError("duplicate_stream_finish")
                self.finish_reason = reason
        usage = value.get("usage")
        if usage is not None:
            if self.usage is not None or not valid_usage(usage):
                raise ValueError("duplicate_or_invalid_stream_usage")
            self.usage = usage
        self.notify("sse_chunk", {"operation": self.operation, "chunk": value})

    def feed(self, chunk):
        self.total += len(chunk)
        if self.total > 2 * 1024 * 1024:
            raise ValueError("stream_response_byte_budget")
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            if line.endswith(b"\r"):
                line = line[:-1]
            if not line:
                self.event()
            elif line.startswith(b"data:"):
                data = line[5:]
                if data.startswith(b" "):
                    data = data[1:]
                self.lines.append(data)
                self.frame_bytes += len(data)
                if self.frame_bytes > 1024 * 1024:
                    raise ValueError("stream_frame_byte_budget")
        if len(self.pending) > 1024 * 1024:
            raise ValueError("stream_line_byte_budget")

    def finish(self):
        if not self.done:
            raise ValueError("stream_incomplete_or_cancelled")

    def close(self):
        if not self.closed and not self.done:
            self.notify("failure", {"operation": self.operation, "reason": "stream_abandoned"})
        self.closed = True


def budget_transport(ledger, *, model, endpoint, underlying, on_event: Callable | None = None):
    expected = httpx.URL(endpoint.rstrip("/") + "/chat/completions")
    notify = on_event or (lambda *args: None)

    class Audited(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            operation = OPERATION.get()
            try:
                body = await request.aread()
                payload = strict_json(body)
                if request.method != "POST" or request.url != expected or payload.get("model") != model:
                    raise ValueError("unexpected_provider_request")
                notify("request", {"operation": operation, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                                   "stream": payload.get("stream", False)})
                ledger.reserve(operation, body)
                response = await underlying.handle_async_request(request)
                if payload.get("stream") is not True or response.status_code >= 400:
                    await response.aread()
                    try:
                        value = strict_json(response.content)
                    except Exception:
                        ledger.settle(operation, httpx.Response(response.status_code, json={}))
                        raise ValueError("provider_json_invalid") from None
                    ledger.settle(operation, httpx.Response(response.status_code, json=value))
                    notify("json_response", {"operation": operation, "status": response.status_code, "response": value})
                    if response.status_code >= 400:
                        raise ValueError("provider_http_error_" + str(response.status_code))
                    if not valid_usage(value.get("usage")):
                        raise ValueError("provider_usage_missing_or_invalid")
                    return response
                if "text/event-stream" not in response.headers.get("content-type", "").lower():
                    await response.aclose()
                    raise ValueError("stream_content_type_invalid")
                audit = SSEAudit(ledger, operation, response.status_code, notify)

                class Body(httpx.AsyncByteStream):
                    def __init__(self):
                        self.iterator = response.aiter_bytes()
                        self.closed = False
                    def __aiter__(self):
                        return self
                    async def __anext__(self):
                        try:
                            chunk = await self.iterator.__anext__()
                            audit.feed(chunk)
                            return chunk
                        except StopAsyncIteration:
                            try:
                                audit.finish()
                            finally:
                                await self.aclose()
                            raise
                        except BaseException as exc:
                            notify("failure", {"operation": operation, "reason": type(exc).__name__ + ":" + str(exc)})
                            await self.aclose()
                            raise
                    async def aclose(self):
                        if self.closed:
                            return
                        self.closed = True
                        audit.close()
                        try:
                            await self.iterator.aclose()
                        finally:
                            await response.aclose()
                return httpx.Response(response.status_code, headers={k:v for k,v in response.headers.items() if k.lower() not in {"content-encoding","content-length"}}, stream=Body(), request=request)
            except BaseException as exc:
                notify("failure", {"operation": operation, "reason": type(exc).__name__ + ":" + str(exc)})
                raise
        async def aclose(self):
            await underlying.aclose()
    return Audited()
