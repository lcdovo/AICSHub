"""排障反馈的有限来源检查；检查过不等于正常，规则不替代语义Judge。"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.state import AgentState

DIAGNOSTIC_POLICY_VERSION = "diagnostic-feedback.v1"
COMPONENTS = {
    "power": r"电源|供电",
    "indicator": r"指示灯",
    "connection": r"网络连接|数据连接|连接",
}
_CLAUSES = re.compile(r"[，,。；;！？!?\n]|但是|不过|但")
_NONASSERTION = re.compile(r"如果|假如|若|是否|能否|吗|以前|之前|曾经|原来|据说|客服说|[“”\"「」]")
_NEGATION = re.compile(r"不是|并非|不能|不代表|不表示|无法|尚未|还未|没有确认|未确认|不正常|(?<!无)异常")


def diagnostic_feedback_contract(state: AgentState) -> dict | None:
    if not state.task or not state.task_runtime or not (state.task.kind == "troubleshoot"
            or any(item.kind == "troubleshoot" for item in state.task.items)):
        return None
    return current_feedback_report(state.user_message)


def current_feedback_report(text: str) -> dict:
    # Only this turn's explicit user report can support a present observation.
    # Old assistant answers, knowledge instructions and task hints cannot do so.
    components = []
    for name, subject in COMPONENTS.items():
        normal_reports = []
        for clause in _CLAUSES.split(text):
            if not re.search(subject, clause) or _NONASSERTION.search(clause):
                continue
            normal_reports = []  # A later statement about the same component supersedes it.
            if _NEGATION.search(clause):
                continue
            if re.search(rf"(?:{subject})(?:已|已经|确认|确定|检查|核实|是|都|目前|现在|了|\s){{0,8}}"
                         r"(?:正常|没问题|没有问题|无异常)", clause):
                normal_reports.append(clause.strip())
        components.append({"component": name, "current_user_reported_normal": bool(normal_reports),
                           "quotes": normal_reports})
    return {"version": DIAGNOSTIC_POLICY_VERSION, "current_user_report": text,
            "components": components,
            "rule": "检查动作不证明结果正常；用户报告正常也不证明已排除该故障源；"
                    "未知结果应补问，引用反馈时说明来自用户，不把历史助手或知识指引当实际观察。"}


def diagnostic_claim_issues(response: str, contract: dict | None) -> list[str]:
    if contract is None:
        return []
    normal = {item["component"]: item["current_user_reported_normal"] for item in contract["components"]}
    issues = []
    # A question about a result is not an assertion of that result.
    response = re.sub(r"[^。；;！？!?\n]+[？?]", "", response)
    for clause in _CLAUSES.split(response):
        if _NONASSERTION.search(clause) or _NEGATION.search(clause):
            continue
        # Questions and instructions request a result; they do not assert it.
        if re.search(r"请(?:先|再|您|你)?(?:检查|确认|观察|核实)|需要(?:检查|确认)|待(?:确认|核实)", clause):
            continue
        for component, subject in COMPONENTS.items():
            if re.search(rf"(?:{subject})[^，,。；;！？!?\n]{{0,10}}(?:已(?:经)?排除|排除(?:了)?)", clause) or re.search(
                    rf"(?:已(?:经)?排除|排除(?:了)?)[^，,。；;！？!?\n]{{0,6}}(?:{subject})", clause):
                issues.append("diagnostic_unproven_elimination")
            if not normal.get(component, False) and re.search(
                    rf"(?:{subject})(?:这一步|部分|方面|的状态)?"
                    r"(?:已|已经|确认|确定|检查|核实|是|为|都|确实|目前|现在|了|\s){0,8}"
                    r"(?:正常|没问题|没有问题|无异常)", clause):
                issues.append("diagnostic_unobserved_normal:" + component)
    return list(dict.fromkeys(issues))
