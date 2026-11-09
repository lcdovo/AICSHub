"""所有响应（包括workflow）发布前的确定性底线；不宣称通用语义事实审查。"""
import re

from app.services.llm_governance import SensitiveDataRedactor
from app.agent.claim_scope import has_affirmed_claim

SAFE_PUBLICATION_REPLY = "本轮回复未通过事实或安全核验。请补充问题信息或查看已有业务记录。"


def publication_issues(response: str) -> list[str]:
    """仅返回稳定错误码，不将敏感候选写进状态、Trace或审查日志。"""
    issues = []
    redacted = response
    for pattern, replacement in (*SensitiveDataRedactor.PATTERNS, *SensitiveDataRedactor.UNICODE_PATTERNS):
        redacted = pattern.sub(replacement, redacted)
    if redacted != response:
        issues.append("sensitive_output")
    # 当前产品没有实时真人会话或实时队列；登记/分配不证明已接通。
    affirmative = re.sub(r"(?:尚未|还未|并未|没有|未|无法|不能)(?:为您)?接通(?:人工|真人)?(?:客服)?", "", response)
    if re.search(r"(?:人工|真人)(?:客服)?(?:已经|已)?接通|(?:已经|已)(?:为您)?接通(?:了)?(?:人工|真人)", affirmative):
        issues.append("unsupported_live_connection")
    if re.search(r"预计(?:等待|等候).{0,4}\d+\s*(?:分钟|小时|秒)|\d+\s*(?:人|位).{0,5}排队", response):
        issues.append("unsupported_wait_estimate")
    # 聊天工具没有授予/修改访问权限的能力，候选自称完成也不是授权回执。
    if has_affirmed_claim(response,
        r"(?:已经|已)(?:为您|替您|帮您)?(?:打开|开启|开通|授予|赋予|修改|绕过|跳过)"
        r"[^。；;，,\n]{0,32}(?:权限|授权|访问控制)"):
        issues.append("unsupported_permission_change")
    if not response.strip():
        issues.append("empty_output")
    return issues
