"""M5新授权的持久HTTP派发账本；未知/失败不退额度，不自动重试。"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

OPERATION = ContextVar("m5_evaluation_operation", default=None)


def validate_authorization(value: dict, plan: dict) -> dict:
    if (value.get("plan_id") != plan["plan_id"] or value.get("approved_by") != "user"
            or not str(value.get("approved_at", "")).strip()
            or not str(value.get("user_statement", "")).strip()):
        raise ValueError("new_explicit_user_authorization_required")
    limits = {}
    for name in ("max_requests", "max_input_tokens", "max_output_tokens"):
        limit = value.get(name)
        if type(limit) is not int or not 0 < limit <= plan["proposed_limits"][name]:
            raise ValueError("authorization_limit_invalid")
        limits[name] = limit
    return limits


class DispatchLedger:
    def __init__(self, path: Path, plan_id: str, limits: dict, operations: dict[str, int]):
        self.path, self.plan_id, self.limits, self.operations = path, plan_id, limits, operations
        marker = path.with_suffix(".created.json")
        if path.exists() != marker.exists():
            raise ValueError("ledger_or_creation_marker_missing_no_reset_allowed")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with marker.open("x", encoding="utf-8") as stream:
                json.dump({"plan_id": plan_id, "limits": limits}, stream)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
                db.execute("INSERT INTO metadata VALUES (1, ?)", (json.dumps({"plan_id": plan_id, "limits": limits}, sort_keys=True),))
                db.execute("""CREATE TABLE dispatches (
                    operation TEXT PRIMARY KEY, input_reserved INTEGER NOT NULL, output_reserved INTEGER NOT NULL,
                    request_sha256 TEXT NOT NULL, status TEXT NOT NULL,
                    input_actual INTEGER, output_actual INTEGER, http_status INTEGER)""")
        expected = {"plan_id": plan_id, "limits": limits}
        if json.loads(marker.read_bytes()) != expected:
            raise ValueError("ledger_authorization_changed")
        with closing(sqlite3.connect(path)) as db, db:
            row = db.execute("SELECT value FROM metadata WHERE id=1").fetchone()
            if not row or json.loads(row[0]) != expected:
                raise ValueError("ledger_authorization_changed")
            if any(row[0] not in operations for row in db.execute("SELECT operation FROM dispatches")):
                raise ValueError("ledger_operation_plan_mismatch")

    def reserve(self, operation: str, body: bytes) -> None:
        payload = json.loads(body)
        output = payload.get("max_completion_tokens", payload.get("max_tokens"))
        if (operation not in self.operations or type(output) is not int
                or not 0 < output <= self.operations[operation] or len(body) > 24576):
            raise ValueError("request_outside_operation_budget")
        if payload.get("stream") is True:
            raise ValueError("evaluation_stream_not_authorized")
        # 完整HTTP JSON的UTF-8字节作为保守输入预留，不冒充真实token用量。
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM dispatches WHERE operation=?", (operation,)).fetchone():
                raise ValueError("operation_already_dispatched_no_retry")
            count, inp, out = db.execute("SELECT count(*), coalesce(sum(input_reserved),0), coalesce(sum(output_reserved),0) FROM dispatches").fetchone()
            if (count + 1 > self.limits["max_requests"] or inp + len(body) > self.limits["max_input_tokens"]
                    or out + output > self.limits["max_output_tokens"]):
                raise ValueError("authorized_budget_exhausted")
            db.execute("INSERT INTO dispatches VALUES (?, ?, ?, ?, 'unknown_dispatched', NULL, NULL, NULL)",
                       (operation, len(body), output, hashlib.sha256(body).hexdigest()))

    def settle(self, operation: str, response) -> None:
        try:
            usage = response.json().get("usage", {})
            inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
        except Exception:
            inp = out = None
        actual = type(inp) is int and type(out) is int and inp >= 0 and out >= 0
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE dispatches SET status=?, input_actual=?, output_actual=?, http_status=? WHERE operation=?",
                       ("http_complete" if actual else "http_complete_usage_unknown", inp if actual else None,
                        out if actual else None, response.status_code, operation))

    def dispatched(self) -> set[str]:
        with closing(sqlite3.connect(self.path)) as db, db:
            return {row[0] for row in db.execute("SELECT operation FROM dispatches")}

    def snapshot(self) -> dict:
        with closing(sqlite3.connect(self.path)) as db, db:
            count, inp, out, actual_inp, actual_out, unknown = db.execute("""SELECT count(*),
                coalesce(sum(input_reserved),0),coalesce(sum(output_reserved),0),
                coalesce(sum(input_actual),0),coalesce(sum(output_actual),0),
                coalesce(sum(input_actual IS NULL OR output_actual IS NULL),0) FROM dispatches""").fetchone()
        return {"requests": count, "input_reserved_upper_bound": inp, "output_reserved": out,
                "known_actual_input_tokens": actual_inp, "known_actual_output_tokens": actual_out,
                "usage_unknown_requests": unknown, "limits": self.limits,
                "input_basis": "full_http_json_utf8_bytes_upper_bound_not_provider_usage"}


def guarded_transport(ledger: DispatchLedger, *, model: str, endpoint: str, underlying=None):
    import httpx
    expected = httpx.URL(endpoint.rstrip("/") + "/chat/completions")

    class Transport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = underlying if underlying is not None else httpx.AsyncHTTPTransport(retries=0)

        async def handle_async_request(self, request):
            operation = OPERATION.get()
            body = await request.aread()
            if request.method != "POST" or request.url != expected or json.loads(body).get("model") != model:
                raise ValueError("unexpected_provider_request")
            ledger.reserve(operation, body)  # 提交到SQLite后才允许进入真实传输。
            response = await self.inner.handle_async_request(request)
            await response.aread()
            ledger.settle(operation, response)
            return response

        async def aclose(self):
            await self.inner.aclose()

    return Transport()
