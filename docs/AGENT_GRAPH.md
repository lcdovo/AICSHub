# Agent 主链路（第 4 步）

## 编排入口

`app.agent.graph.AgentGraph` 只保留一张编译后的 LangGraph：`self.langgraph`。
`run()` 使用 `ainvoke`，`run_stream()` 使用同一张图的 `astream(..., stream_mode="updates")`，流式方法只负责把图更新转换为 WebSocket 事件，不再复制节点循环、路由或响应校验逻辑。

节点顺序由意图决定：

`start -> intent_recognition -> (clarification | rag_retrieval | tool_execution | response_generation | human_handoff) -> result_verification -> response_generation -> end`

退款在工具执行前增加 `confirmation` 节点。首次请求只写入 `refund_preview` 和 `pending_confirmations`，回复明确的“确认/同意/确定/提交”等指令后，才允许调用 `apply_refund`。

## 工具契约

业务工具通过 `app.agent.langchain_tools` 转换为 LangChain `StructuredTool`，统一使用 Pydantic 参数 Schema 和 `ainvoke` 调用；底层 `BaseTool.execute_with_retry` 提供参数校验、超时、指数退避、错误分类、幂等缓存和简易熔断。

订单查询和退款工具接收资源所属 `user_id`，不匹配时返回 `permission` 错误。退款工具支持 `refund`、`return`、`exchange` 三种类型；Agent 生成的换货意图不再触发枚举错误。退款调用使用会话/订单/类型组合的幂等键。

## 流式降级标记

当前 Mock/非流式供应商没有真实 token 流。`run_stream()` 在首个事件返回 `token_stream: "simulated"`，切片 token 事件带 `simulated: true`，避免把模拟切片宣称为供应商 streaming。

