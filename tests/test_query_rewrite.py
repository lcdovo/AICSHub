"""保守查询改写单元测试：上下文拼接、指代消解、订单号提取。

验证 ConservativeQueryRewriter 的核心规则逻辑：
1. 自包含问题不改写（直接返回原句）
2. 含指代/短追问（"这个""那""然后呢"）补上前一轮问题上下文
3. 从之前轮次提取订单号拼到当前问题
4. LLM 组合改写器：规则判定"自包含"时短路，不再触发 LLM 复审；规则无法改写（无上轮上下文）时保留 LLM 兜底
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


def _messages(current: str, prev: str = "我想给蓝牙耳机申请退货退款"):
    return [{"role": "user", "content": prev}, {"role": "user", "content": current}]


def test_punctuated_short_follow_up():
    """带标点的裸短问（价格呢？）应触发拼接（去标点后判 short_follow_up）"""
    result = rewriter.rewrite("价格呢？", messages=_messages("价格呢？"))
    assert result.applied
    assert "蓝牙耳机" in result.rewritten_query


def test_bare_noun_follow_up():
    """光杆名词追问（运费谁出？/售后呢？）依赖上文，应触发拼接"""
    for q in ("运费谁出？", "售后呢？", "哪个更好用", "大概多久？", "怎么操作呀"):
        result = rewriter.rewrite(q, messages=_messages(q))
        assert result.applied, q
        assert "蓝牙耳机" in result.rewritten_query, q


def test_full_question_not_rewritten():
    """完整问句不应被误判为短追问拼接上文（避免污染 RAG 检索）"""
    for q in ("满减活动和优惠券能叠加使用吗", "签收后7天内可以无理由退货吗", "可以退到余额里吗"):
        result = rewriter.rewrite(q, messages=_messages(q))
        assert not result.applied, q
        assert result.reason == "self_contained_query", q


def _make_llm_rewriter(fake_llm):
    """构造 LLMQueryRewriter，用 fake LLM 校验"是否被调用"。"""
    from app.agent.query_rewrite_llm import LLMQueryRewriter

    return LLMQueryRewriter(llm_service=fake_llm)


class _SpyLLM:
    """记录 run_structured 是否被调用，模拟可用但不产出改写。"""

    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    async def run_structured(self, *args, **kwargs):
        self.calls += 1
        return None


def test_self_contained_skips_llm():
    """规则判定自包含（无需改写）→ 短路，不再触发 LLM 复审。"""
    spy = _SpyLLM()
    rewriter_llm = _make_llm_rewriter(spy)
    result = __import__("asyncio").run(
        rewriter_llm.rewrite(
            "退货政策是什么",
            messages=[
                {"role": "user", "content": "你好"},
                {"role": "user", "content": "退货政策是什么"},
            ],
        )
    )
    assert not result.applied
    assert result.reason == "self_contained_query"
    assert spy.calls == 0, "自包含改写不应触发 LLM 调用"


def test_no_previous_context_calls_llm():
    """规则无法改写（无上轮上下文可拼接）→ 保留 LLM 语义补全兜底。"""
    spy = _SpyLLM()
    rewriter_llm = _make_llm_rewriter(spy)
    result = __import__("asyncio").run(
        rewriter_llm.rewrite("那现在到哪了呢", messages=[])
    )
    assert not result.applied
    assert result.reason == "no_previous_user_query"
    assert spy.calls == 1, "规则无法改写时应保留 LLM 兜底调用"
