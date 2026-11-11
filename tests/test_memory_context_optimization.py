"""有来源摘要、空召回与隐式追问；全部使用合成数据及离线依赖替身。"""
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent.request_context import build_request_context, stage_context
from app.agent.state import AgentState
from app.agent.task_contracts import TaskProgress
from app.services.conversation_summary import (
    MAX_STORED_CHARS, MODEL_SUMMARY_CHARS, SUMMARY_POLICY, parse_summary,
    summary_context, update_summary,
)
from app.services.user_memory import UserMemoryService


def row(message_id, text, role="user"):
    return SimpleNamespace(id=message_id, content=text, role=SimpleNamespace(value=role))


def test_structured_summary_retains_late_negation_conditions_and_sources():
    condition = "订单的情况如下：" + "我补充一些背景，" * 12 + "只有包装坏了，商品没有坏，不要按质量问题退款。"
    summary = update_summary(None, [row(1, condition), row(2, "请检查指示灯后告诉我结果。", "assistant"),
                                   row(3, "检查过了，红灯闪两次，问题没有解决。"),
                                   row(4, "运费谁承担？")])
    parsed = parse_summary(summary)
    assert parsed.policy == SUMMARY_POLICY
    assert parsed.sections["conditions"][0].text == condition
    assert parsed.sections["conditions"][0].message_id == 1
    assert parsed.sections["suggested_steps"][0].role == "assistant"
    assert parsed.sections["feedback"][0].message_id == 3
    payload = summary_context(summary)
    assert condition in json.dumps(payload, ensure_ascii=False)
    assert payload["trusted_as_current_evidence"] is False and payload["grants_authorization"] is False


def test_summary_keeps_early_constraint_across_unrelated_chatter_and_correction():
    summary = update_summary(None, [row(1, "只有包装破损，商品没有坏。")])
    for start in range(2, 82, 10):
        summary = update_summary(summary, [row(i, f"合成闲聊记录{i}") for i in range(start, start + 10)])
    summary = update_summary(summary, [row(90, "更正：商品也有破损，请按这次说明理解。")])
    payload = summary_context(summary)
    flattened = [item for items in payload["sections"].values() for item in items]
    assert any(item["message_id"] == 1 and "没有坏" in item["text"] for item in flattened)
    assert any(item["message_id"] == 90 and "更正" in item["text"] for item in flattened)
    assert payload["omitted_excerpts"] > 0


def test_summary_limits_do_not_cut_json_or_conditional_sentence():
    condition = "如果" + "完整条件" * 100 + "；只有确认满足后才讨论退款，否则不要申请。"
    summary = update_summary(None, [row(i, condition + str(i)) for i in range(1, 45)])
    assert len(summary) <= MAX_STORED_CHARS
    payload = summary_context(summary)
    excerpts = [item for items in payload["sections"].values() for item in items]
    assert all(item["text"] == condition + str(item["message_id"]) for item in excerpts)
    assert sum(len(json.dumps(item, ensure_ascii=False)) for item in excerpts) <= MODEL_SUMMARY_CHARS
    assert parse_summary(summary).omitted_excerpts > 0


def test_oversized_single_sentence_is_omitted_explicitly_instead_of_cutting_negation():
    summary = update_summary(None, [row(1, "如果" + "条件" * 600 + "，不要退款。"), row(2, "商品没有损坏。")])
    parsed = parse_summary(summary)
    assert parsed.omitted_excerpts == 1
    assert parsed.sections["conditions"][0].text == "商品没有损坏。"


def test_summary_legacy_compatibility_and_redaction():
    summary = update_summary("旧摘要，仅作背景", [row(2, "联系电话13800138000，不要拨错。")])
    assert parse_summary(summary).legacy_context == "旧摘要，仅作背景"
    assert "13800138000" not in summary
    assert summary_context("旧纯文本摘要")["text"] == "旧纯文本摘要"


def history(content, message_id=1, **kwargs):
    return {"role": "user", "content": content, "message_id": message_id, "session_id": "old",
            "source_type": "historical_user_statement", "current": False, **kwargs}


@pytest.mark.parametrize("query,old", [
    ("耳机如何连接电脑", "打印机如何添加纸张"),
    ("请问退款需要什么", "请问怎么安装"),
    ("怎么", "怎么退款"), ("退", "我想退款"), ("？？", "安装说明"),
])
async def test_keyword_recall_can_be_empty_and_ignores_generic_question_words(monkeypatch, query, old):
    monkeypatch.setattr("app.services.embedding_service.get_embedding_service", lambda: Mock(get_embedding=lambda: None))
    recalled, _ = await UserMemoryService()._recall(query, [history(old)])
    assert recalled == []


async def test_keyword_fallback_requires_match_and_excludes_assistant_and_current_session(monkeypatch):
    monkeypatch.setattr("app.services.embedding_service.get_embedding_service", Mock(side_effect=RuntimeError("synthetic")))
    rows = [history("耳机退款咨询", 1), history("打印机安装", 2),
            history("耳机退款咨询", 3, current=True), history("耳机退款咨询", 4, role="assistant")]
    recalled, backend = await UserMemoryService()._recall("退款", rows)
    assert backend == "keyword"
    assert [item["message_id"] for item in recalled] == [1]


@pytest.mark.parametrize("vectors,expected", [
    ([[0., 1.], [0.5, 0.866]], []),
    ([[0.8, 0.6], [0., 1.]], [1]),
    ([[float("nan"), 0.], [0., 0.]], []),
])
async def test_vector_gate_rejects_irrelevant_and_invalid_scores_without_keyword_refill(monkeypatch, vectors, expected):
    embedding = SimpleNamespace(get_embedding=lambda: object(), async_encode_query=AsyncMock(return_value=[1., 0.]))
    monkeypatch.setattr("app.services.embedding_service.get_embedding_service", lambda: embedding)
    service = UserMemoryService()
    service._encode_with_cache = AsyncMock(return_value=vectors)
    recalled, backend = await service._recall("退款", [history("退款资料", 1), history("退款流程", 2)])
    assert backend == "vector"
    assert [item["message_id"] for item in recalled] == expected


async def test_semantic_synonym_without_literal_match_can_pass_threshold(monkeypatch):
    embedding = SimpleNamespace(get_embedding=lambda: object(), async_encode_query=AsyncMock(return_value=[1., 0.]))
    monkeypatch.setattr("app.services.embedding_service.get_embedding_service", lambda: embedding)
    service = UserMemoryService(min_similarity=.8)
    service._encode_with_cache = AsyncMock(return_value=[[.9, .1]])
    recalled, _ = await service._recall("退款", [history("申请返还货款")])
    assert len(recalled) == 1


def test_memory_factory_respects_zero_items_and_configured_threshold(monkeypatch):
    import app.services.user_memory as memory
    from app.config.config import settings

    monkeypatch.setattr(memory, "_service_instance", None)
    monkeypatch.setattr(settings, "USER_MEMORY_MAX_ITEMS", 0)
    monkeypatch.setattr(settings, "USER_MEMORY_MIN_SIMILARITY", .75)
    assert memory.get_user_memory_service().max_items == 0
    assert memory.get_user_memory_service().min_similarity == .75


def context_state(message, previous="耳机包装破损但商品没坏", *, task=None):
    return AgentState(session_id="memory-context", user_id=101, actor_id=101, user_message=message,
                      messages=[{"role": "user", "content": previous}, {"role": "user", "content": message}],
                      collected_info={"order_id": "ORD940001"}, task=task)


def pending_task(**kwargs):
    values = dict(task_id="task1", session_id="memory-context", user_id=101, actor_id=101,
                  goal="路由器蓝牙连接失败，请帮助排障", kind="troubleshoot", product="路由器",
                  status="waiting_input", waiting_for="diagnostic_feedback")
    return TaskProgress(**{**values, **kwargs})


@pytest.mark.parametrize("message", ["运费谁承担？", "退回需要什么材料", "包装破损是否能换货？", "运费能退吗"])
def test_related_followup_without_reference_words_keeps_background(message):
    current = context_state(message)
    assert build_request_context(current).history


def test_pending_task_restores_implicit_feedback_even_when_recent_history_is_empty():
    current = context_state("红灯闪两次", task=pending_task())
    current.messages = [{"role": "user", "content": current.user_message}]
    payload = stage_context(current)
    assert payload["history"][0]["content"] == current.task.goal
    assert payload["task_memory"]["grants_authorization"] is False
    assert current.current_evidence() == [] and current.tool_calls == []


@pytest.mark.parametrize("message", [
    "我还有另一个问题：它如何连接？", "给另一个订单退款", "ORD940002物流多久能到？",
    "打印机蓝牙如何连接？", "会员优惠活动有哪些？", "换个话题，还是说退款吧",
])
def test_topic_or_object_switch_does_not_resurrect_pending_task_or_known_order(message):
    current = context_state(message, previous="路由器蓝牙连接失败", task=pending_task())
    payload = stage_context(current)
    assert payload["history"] == [] and payload["known_order"] is None
    assert "task_memory" not in payload and "historical_summary" not in payload


@pytest.mark.parametrize("identity", [{"user_id": 202}, {"session_id": "another"}, {"actor_id": 202}])
def test_foreign_task_cannot_supply_context(identity):
    current = context_state("红灯闪两次", previous="", task=pending_task(**identity))
    assert build_request_context(current).history == ()
    assert "task_memory" not in stage_context(current)


def test_unrelated_question_does_not_expose_pending_refund_hint(monkeypatch):
    monkeypatch.setattr(AgentState, "refund_preview_data", lambda self: {"action_id": "synthetic-preview"})
    current = context_state("会员优惠有哪些？", task=pending_task())
    context = build_request_context(current)
    assert context.history == () and context.known_order is None and not context.pending_action


async def test_zero_similarity_never_admits_empty_or_incompatible_vectors(monkeypatch):
    embedding = SimpleNamespace(get_embedding=lambda: object(), async_encode_query=AsyncMock(return_value=[1., 0.]))
    monkeypatch.setattr("app.services.embedding_service.get_embedding_service", lambda: embedding)
    service = UserMemoryService(min_similarity=0)
    service._encode_with_cache = AsyncMock(return_value=[[]])
    assert await service._recall("退款", [history("退款")]) == ([], "vector")


async def test_sql_summary_recovery_preserves_conditions_and_excludes_deleted_sessions(tmp_path, monkeypatch):
    from app.models.models import Message, MessageRole, Session
    from app.services import background_jobs as jobs
    from app.services.chat_service import ChatService
    from app.agent.memory import SessionManager
    from tests.test_ticket_idempotency import api_context

    async with api_context(tmp_path / "summary-context.db") as (_, factory):
        monkeypatch.setattr("app.utils.database.async_session", factory)
        manager = SessionManager()
        manager._redis_available = False
        monkeypatch.setattr("app.services.chat_service.session_manager", manager)
        async with factory() as db:
            for index in range(30):
                db.add(Message(session_id="session-101", role=MessageRole.USER,
                               content="只有包装破损，商品没有坏。" if index == 0 else f"合成背景{index}"))
            jobs.enqueue(db, "summary", "session-101", 101, "memory-context-1")
            await db.commit()
        await jobs.tick(factory, kind="summary")
        current = await ChatService().hydrate_state("session-101", 101, "那运费谁承担？", recover_from_db=True)
        payload = stage_context(current)
        assert "商品没有坏" in json.dumps(payload["historical_summary"], ensure_ascii=False)
        async with factory() as db:
            session = await db.get(Session, "session-101")
            assert session.summary_policy == SUMMARY_POLICY and session.summary_cursor == 20
            session.deleted_at = datetime.now()
            jobs.enqueue(db, "summary", "session-101", 101, "memory-context-2")
            await db.commit()
        await jobs.tick(factory, kind="summary")
        async with factory() as db:
            assert (await db.get(Session, "session-101")).summary_cursor == 20
