"""保守查询改写单元测试：上下文拼接、指代消解、订单号提取。

验证 ConservativeQueryRewriter 的核心规则逻辑：
1. 自包含问题不改写（直接返回原句）
2. 含指代/短追问（"这个""那""然后呢"）补上前一轮问题上下文
3. 从之前轮次提取订单号拼到当前问题
"""
from app.agent.query_rewrite import ConservativeQueryRewriter

rewriter = ConservativeQueryRewriter()


def test_self_contained_no_rewrite():
    """问题明确且自包含不应改写"""
    result = rewriter.rewrite("退货政策是什么", messages=[])
    assert result.applied is False
    assert result.rewritten_query == "退货政策是什么"


def test_reference_picks_previous_context():
    """含指代性代词，应该补上前一轮上下文"""
    messages = [
        {"role": "user", "content": "退换货政策有什么要求"},
        {"role": "assistant", "content": "退换货遵循..."},
        {"role": "user", "content": "这个产品支持吗"},
    ]
    result = rewriter.rewrite("这个产品支持吗", messages=messages)
    assert result.applied
    assert "退换货政策有什么要求" in result.rewritten_query
    assert "这个产品支持吗" in result.rewritten_query


def test_short_follow_up_concat():
    """短追问（以"那"开头）应该拼上下文"""
    messages = [
        {"role": "user", "content": "我想查一下订单 ORD123456"},
        {"role": "assistant", "content": "请提供订单号"},
        {"role": "user", "content": "那现在到哪了呢"},
    ]
    result = rewriter.rewrite("那现在到哪了呢", messages=messages)
    assert result.applied
    assert "我想查一下订单" in result.rewritten_query
    assert "那现在到哪了呢" in result.rewritten_query


def test_extract_order_id_from_previous():
    """从历史上下文提取订单号拼入当前追问"""
    messages = [
        {"role": "user", "content": "帮我查询一下 ORD123456"},
        {"role": "assistant", "content": "您的订单 ORD123456 已经发货"},
        {"role": "user", "content": "它现在在哪里"},
    ]
    result = rewriter.rewrite("它现在在哪里", messages=messages)
    assert result.applied
    assert "ORD123456" in result.rewritten_query
    assert "帮我查询一下" in result.rewritten_query


def test_no_previous_user_message_no_rewrite():
    """消息列表没有 user 消息不拼接，直接返回"""
    messages = [
        {"role": "assistant", "content": "您好，请问有什么可以帮您"},
        {"role": "user", "content": "你好"},
    ]
    result = rewriter.rewrite("你好", messages=messages)
    # 上一个 user 不存在，找不到 previous query → 不改写
    assert not result.applied
    assert result.rewritten_query == "你好"


def test_cut_at_300_chars():
    """超长拼接截断到 300 字，不溢出 embedding 上下文"""
    prev = "a" * 200 + "这个问题还能再问吗"
    curr = "那个产品还能用吗"  # 当前追问，包含"那个"→触发拼接
    messages = [{"role": "user", "content": prev}]
    result = rewriter.rewrite(curr, messages)
    assert result.applied
    assert len(result.rewritten_query) <= 300
