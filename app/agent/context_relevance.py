"""有界中文上下文关联信号；只选择背景，不推导事实或授权。"""
from __future__ import annotations

import re


_GENERIC = {
    "怎么", "如何", "什么", "可以", "是否", "能否", "需要", "请问", "一下", "帮我",
    "您好", "你好", "谢谢", "你们", "我们", "我的", "这个", "那个", "问题", "想问",
    "还有", "现在", "的是", "了吗", "的吗", "多少", "多久", "已经", "之前", "我想", "想要",
}
_GENERIC_PATTERN = re.compile("|".join(sorted(_GENERIC, key=lambda word: (-len(word), word))))
_TOPICS = {
    "after_sales": ("退款", "退货", "换货", "售后", "运费", "包装", "质量", "破损", "退回", "寄回"),
    "delivery": ("物流", "快递", "发货", "收货", "配送", "签收", "运单", "送达"),
    "technical": ("连接", "配对", "蓝牙", "安装", "开机", "重启", "电源", "指示灯", "故障", "报错"),
    "promotion": ("优惠", "活动", "折扣", "会员", "积分", "优惠券"),
    "complaint": ("投诉", "举报", "工单"),
}
_PRODUCTS = re.compile(r"打印机|耳机|路由器|显示器|摄像头|音箱|手机|电脑|平板|电视|手表|键盘|鼠标|冰箱|洗衣机")


def product_mentions(text: str) -> set[str]:
    """有限常见产品边界；未知名称仍依赖主题/指代和后续结构化理解。"""
    return set(_PRODUCTS.findall(text))


def query_units(text: str) -> set[str]:
    """按中文双字和完整英文/数字词匹配，标点与常见问句不作为关联证据。"""
    result: set[str] = set()
    # 常见问句作为分隔符，避免“耳机如何/打印机如何”因跨词的“机如”产生虚假匹配。
    text = _GENERIC_PATTERN.sub(" ", text.casefold())
    for token in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+(?:[-_][a-z0-9]+)*", text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            result.update(token[i:i + 2] for i in range(len(token) - 1)
                          if token[i:i + 2] not in _GENERIC)
        elif len(token) > 1:
            result.add(token)
    return result


def lexical_matches(query: str, content: str) -> int:
    return len(query_units(query) & query_units(content))


def topics(text: str) -> set[str]:
    return {topic for topic, words in _TOPICS.items() if any(word in text for word in words)}


def related_topic(message: str, previous: str) -> bool:
    """同业务主题仍要求一个具体共同词，避免仅凭宽泛主题沿用对象。"""
    current_topics, previous_topics = topics(message), topics(previous)
    if current_topics and previous_topics and current_topics.isdisjoint(previous_topics):
        return False
    matches = lexical_matches(message, previous)
    return matches >= 2 or bool(matches and current_topics & previous_topics)
