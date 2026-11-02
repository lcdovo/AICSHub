"""START / END 节点：图入口与终点兜底，无业务逻辑。"""
from app.agent.state import AgentState, AgentNode


async def start_node(self: "AgentGraph", state: AgentState) -> str:
    return AgentNode.INTENT_RECOGNITION


async def end_node(self: "AgentGraph", state: AgentState) -> str:
    return AgentNode.END