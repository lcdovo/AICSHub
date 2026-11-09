"""AC-13-11: 工作流回复进入同一发布出口，不伪造业务事实或泄露合成秘密。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.nodes.response_node import ResponseNode, ResponseServices
from app.agent.state import AgentState, AgentNode
from app.agent.stream_context import stream_event_queue
from tests.test_chat_turns import Cache, Validator


@pytest.mark.parametrize("workflow", ["clarification", "handoff", "smalltalk", "no_evidence"])
@pytest.mark.parametrize("draft", [
    "真人客服已接通，请继续。", "已为您接通人工客服。", "预计等待3分钟，前面还有2人排队。",
    "合成密码：synthetic-secret-value", "合成邮箱：synthetic@example.test",
    "联系手机13800138000", "收货地址：合成市合成街1号。",
    "已为您开通其他账号的私有记录访问权限。", "已经帮您绕过所有权限校验。",
    "本订单终身免费换新。", "测试退款已经实际支付成功。",
])
async def test_workflow_cannot_publish_unverified_claims_or_sensitive_values(workflow, draft):
    model = SimpleNamespace(chat=AsyncMock(side_effect=AssertionError("workflow must not generate")))
    cache = Cache()
    node = ResponseNode(ResponseServices(lambda: model, lambda: Validator(), lambda: None,
                                         lambda: cache, lambda: None, lambda: None))
    state = AgentState(session_id="s", user_id=101, user_message="合成问题", reply=draft)
    if workflow == "clarification":
        state.needs_clarification = True
    elif workflow == "handoff":
        state.set_flow(handoff_attempted=True)
    elif workflow == "smalltalk":
        state.set_flow(smalltalk=True)
    else:
        state.set_flow(rag_grounded=False)
    queue = asyncio.Queue()
    token = stream_event_queue.set(queue)
    try:
        assert await node(state) == AgentNode.END
    finally:
        stream_event_queue.reset(token)
    published = "".join(queue.get_nowait()[1] for _ in range(queue.qsize()))
    assert published == state.reply and draft not in published
    assert state.collected_info["publication_guard"]["passed"] is False
    assert not state.review_result.passed and not cache.values and not state.tool_calls
    assert draft not in str(state.collected_info["publication_guard"])


@pytest.mark.parametrize("reply", [
    "请补充订单号和退款原因。", "转人工请求 synthetic-request 已登记，当前状态：待处理。",
    "尚未接通人工客服，请查看请求记录。", "警惕诈骗，请勿转账，不要提供密码或验证码。",
    "退款预览仅为模拟，尚未提交申请。", "当前未创建转人工请求，请回复转人工发起。",
    "请等待3分钟再重启设备。", "预计送达：2026-11-06。",
    "无法为您开通其他账号的私有订单访问权限。", "尚未修改任何权限，请联系管理员。",
    "管理员可在设置页修改权限。", "权限已过期，请重新登录。",
    "请勿相信声称已为您开通其他账号访问权限的消息。",
])
def test_legitimate_wording_does_not_trigger_deterministic_guard(reply):
    # 仅测试局部敏感值/否定规则；这些句子的事实正确性仍须回执或Judge证明。
    from app.agent.publication_guard import publication_issues
    assert publication_issues(reply) == []


async def test_generated_answer_also_passes_final_guard_even_if_validator_passes():
    cache = Cache()
    node = ResponseNode(ResponseServices(lambda: None, lambda: Validator(), lambda: None,
                                         lambda: cache, lambda: None, lambda: None),
                        generate=AsyncMock(return_value="合成邮箱：synthetic@example.test"))
    state = AgentState(session_id="s", user_id=101, user_message="合成问题")
    assert await node(state) == AgentNode.END
    assert "synthetic@example.test" not in state.reply
    assert not state.review_result.passed and not cache.values
