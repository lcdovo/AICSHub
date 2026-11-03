"""START / END 节点：图入口与终点兜底，无业务逻辑。"""
from app.agent.state import AgentState, AgentNode


async def begin_turn(state: AgentState) -> str:
    return AgentNode.INTENT_RECOGNITION


async def finish_turn(state: AgentState) -> str:
    return AgentNode.END


async def start_node(graph, state: AgentState) -> str:
    return await begin_turn(state)


async def end_node(graph, state: AgentState) -> str:
    return await finish_turn(state)
