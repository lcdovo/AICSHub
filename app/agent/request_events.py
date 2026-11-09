"""无需语义模型的确定寒暄；业务前缀不能被当成整句寒暄。"""
import re

SMALLTALK_PATTERNS = {
    "greeting": ["你好", "您好", "hi", "hello", "在吗", "在不在", "哈喽", "嗨", "早上好", "中午好", "下午好", "晚上好", "hey"],
    "thanks": ["谢谢", "感谢", "多谢", "thanks", "thank"],
    "farewell": ["再见", "拜拜", "bye", "回见"],
}


def is_deterministic_smalltalk(message: str) -> bool:
    text = re.sub(r"[\s，,。.!！?？～~]+", "", (message or "").lower())
    return any(text in words for words in SMALLTALK_PATTERNS.values()) or bool(re.fullmatch(
        r"(?:谢谢|感谢|多谢)(?:你|您)?(?:的)?(?:帮忙|帮助|解答|耐心解答)?", text))
