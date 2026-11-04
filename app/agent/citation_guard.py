"""内联引用身份核验；存在的引用仍须Judge检查其是否支持论断。"""
import re


def citation_issues(response: str, evidence: list[dict]) -> list[str]:
    references = re.findall(r"\[(?:证据|引用|source|evidence)[:：]\s*([^\]\s]+)\]", response, flags=re.I)
    allowed = {(str(item.get("id", "")), str(item.get("version_id", ""))) for item in evidence}
    return ["invalid_citation_identity"] if any(
        "@" not in ref or tuple(ref.rsplit("@", 1)) not in allowed for ref in references
    ) else []
