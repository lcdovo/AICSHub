"""Agent 节点实现包（从 graph.py 拆分）。

13 个 LangGraph 节点的业务编排按域放置在本包：

- start_node.py    : START / END 出入口
- intent_node.py   : 意图识别、路由决策、槽位补齐、澄清
- rag_node.py      : 查询改写、RAG 混合检索
- tool_node.py     : 工具执行、人工确认、结果校验
- response_node.py : 响应生成、引用/缓存/模板、LLM prompt 构建
- handoff_node.py  : 人工转接

graph.py 仅保留 LangGraph 装配与路由表，并通过类属性绑定引用本包函数。
所有节点函数以 ``self`` 作为首参（AgentGraph 实例），保持原方法调用语义。
"""