"""固定业务论断的局部否定范围；有界句式保护，不替代语义Judge。"""
import re


def has_affirmed_claim(text: str, pattern: str) -> bool:
    """检查匹配前的局部否定；转折后的肯定不能被前一个否定豁免。"""
    for match in re.finditer(pattern, text):
        prefix = re.split(r"[。；;，,\n]|但是|然而|不过|可是|但|却", text[:match.start()])[-1]
        negated = re.search(
            r"(?:尚未|还未|并未|没有|无法|不能|不是|不代表|不表示|不承诺|不保证|不提供|不支持|不允许|不予|未|不|无)"
            r"[^。；;，,\n]{0,12}$", prefix,
        )
        warning = re.search(r"(?:请勿|不要|不应|不能)(?:轻易)?相信[^。；;，,\n]{0,16}$", prefix)
        if not negated and not warning:
            return True
    return False
