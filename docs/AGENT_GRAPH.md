# Agent 主链路

## 编排入口与轮次边界

`AgentGraph` 编译同一张 LangGraph，`run()` 使用 `ainvoke`，`run_stream()` 使用
`astream(..., stream_mode="updates")`。REST 和 WebSocket 共用 `ChatService`，
每条用户消息都从 `AgentState.for_next_turn()` 创建独立轮次。

- 跨轮保留：历史消息、摘要、意图历史、订单/物流/工单标识。
- 未完成操作：缺参数或待确认时，保留退款原因、类型和精确预览参数。
- 每轮重置：检索证据、用户画像召回、生成草稿、校验、自检、缓存标记、模型元数据、工具结果和追踪。
- 消息和参数深拷贝，避免处理失败或取消时修改上次已保存的状态。
- Redis 快照保留 `missing_slots` 和 `needs_clarification`，支持进程切换后继续补充参数。

正常政策链路：

`start → intent_recognition → route_decision → query_rewrite → rag_retrieval → response_generation → end`

工具链路：

`route_decision → slot_filling / tool_execution → confirmation / result_verification → response_generation`

`result_verification` 检查工具执行状态；最终文本统一在响应节点校验。

## 退款确认

首次请求生成 `refund_preview` 和 `pending_confirmations`，在 `confirmation` 节点中断。
下一条用户消息通过干净轮次重新判定，REST 与 WebSocket 使用相同路径；
`resume()` 兼容入口同样创建干净轮次，不重新使用旧轮次的生成结果。

仅本条明确确认消息授权执行；历史中的“确认”不授权新的退款。执行参数取自
待确认的精确预览，成功后清除预览和确认标记。取消不会执行退款。
LangGraph `Interrupt` 对象不写入 JSON 会话快照；中断提示也保存到会话历史。

业务工具经 MCP `invoke_tool` 调用，协议连接不可用时回退进程内实现。
工具保留权限校验、参数校验、超时、重试和幂等控制。

M1-06的`create_ticket`已改为共享持久创建服务：REST/WS将已提交用户消息ID与真实actor注入本轮独立字段，工具节点签发绑定owner/actor/消息/参数摘要的短期内部凭据；MCP与直调验证后共用数据库事务。成功回执来自真实Ticket，不再生成假分配或SLA承诺。凭据不写入工具历史/Trace，`for_next_turn`不继承来源消息ID与actor。

创建工单工具关闭进程内成功缓存且只自动尝试一次；同一源消息的MCP响应丢失回退依靠数据库唯一键和请求摘要重读回执。此保证不推广到其他写工具，也不等于客户端重复帧去重或完整Run恢复。原有转人工、退款、通知工具的剩余假成功和未知结果风险仍待后续切片处理。

M1-07的`update_ticket_status`与管理员HTTP更新共用事务、既有状态机与审计。工具需要独立的update_ticket_status用途内部凭据，执行时重查管理员角色；没有普通聊天自动选用或签发更新凭据的新路径。更新采用原字段条件写入、持久键与操作回执，依赖故障不构造“已更新”；note保存为审计备注，不替代resolution。同键重放返回原提交快照，最新状态另查详情。工具只自动尝试一次，不使用进程内成功缓存；不能据此宣称完整并发/恢复验收通过。

## 回答缓存

查询改写和当前证据检索始终先执行，取消意图路由阶段的回答缓存早退。
回答缓存只用于有检索证据、没有业务副作用且通过最终校验的回答。
键使用 SHA-256，包含用户、对话上下文、摘要、改写查询、实际提示词、证据内容及版本、
模型配置、Prompt/Schema/Route 版本和 Judge 开关。同一查询在不同上下文或证据下不会共用答案。

缓存存储结构为 `schema=2, validated=true, reply=最终正文及引用`，旧字符串缓存不再读取。
拒答、转人工及模型降级回复不写缓存。缓存命中仍执行确定性文本校验，避免再次生成；
由于键包含当前证据和校验策略，命中的已校验文本不重复请求 Judge。
Redis 正常时，缓存未命中不会回退读取可能已被其他 worker 失效的本地副本；
Redis 不可用时保留进程内缓存降级。

## 最终校验与输出

所有有证据的政策回答均可进入独立 Judge，包括退款和投诉意图。生成模型的内联自检
只保留为兼容元数据。Judge 未启用或不可用时，沿用确定性事实、安全和完整性检查。

引用片段不计入正文质量检查。重生成最多一次，替换后的回答重新执行完整校验链；
证据不通过使用拒答提示，其余检查仍失败则转人工。人工转接后的流程提示终止本轮，
避免“校验失败 → 转人工 → 再次校验失败”的循环。仅校验通过后追加引用和写入缓存。

WebSocket 继续发送节点事件；供应商流式草稿在服务端缓冲。最终答案通过校验后
按 64 字符分片发布 `token`，`stream_mode.token_stream` 和 token 的 `provider` 标为
`validated`。这增加可见首字等待时间，但不会把随后被拒绝的草稿暴露给用户。
`done.reply` 为最终答案，`stream_end` 表示持久化完成。

## 会话并发与故障边界

REST 与 WebSocket 都在同一个 `session_lock` 中执行：刷新会话 → 写用户消息 →
恢复状态 → Agent 执行 → 持久化回复/评测 → 回写状态。等待锁前结束旧数据库读事务，
获得锁后刷新会话，避免使用上一轮之前的计数或状态。

进程内锁负责同进程串行；Redis 可用时增加 UUID 所有者令牌、90 秒租约、周期续期，
解锁时用 Lua 比较令牌再删除。锁超时返回 409；共享状态/锁错误或租约丢失返回 503。
执行前和持久化前检查所有权，续期失败会取消仍在执行的轮次。Redis 可用时始终读共享
会话快照，不用短时间本地命中覆盖另一 worker 刚写入的状态。

Redis 不可用时只保证单进程串行，不能保证跨 worker 互斥或进程重启恢复。
租约丢失无法回滚已完成的外部动作，退款等副作用仍依赖业务工具幂等键。
LLM 调用元数据使用 ContextVar 隔离，并随 Runnable 返回值显式传回，避免不同用户的
并发调用串用模型、费用或自检结果。

## 回归验证

`tests/test_chat_turns.py` 覆盖跨轮状态清理、缓存隔离与失效、最终稿缓存、拒答不外发、
重生成复核、退款确认跨传输、JSON 快照、并发消息顺序、锁超时、租约丢失和模型元数据隔离。
API 回归使用临时 SQLite 数据库，LLM/MCP/Redis 使用可控替身，不触碰本地业务数据。

## 本次改动文件

- 状态与编排：`app/agent/state.py`、`app/agent/memory.py`、`app/agent/graph.py`。
- 业务节点：`app/agent/nodes/intent_node.py`、`tool_node.py`、`response_node.py`、`handoff_node.py`。
- 服务与接口：`app/services/chat_service.py`、`app/services/llm_service.py`、`app/routers/chat.py`。
- 缓存：`app/utils/redis_cache.py`、`app/services/knowledge_base/cache_invalidation.py`。
- 回归与说明：`tests/test_chat_turns.py`、`tests/test_refund_flow.py`、`docs/AGENT_GRAPH.md`。

本次验证：Python 语法检查通过；聊天状态/缓存/校验/确认/Checkpoint 相关测试 71 项通过；
临时 SQLite API 验证覆盖 `/docs`、REST、WebSocket 和并发消息顺序。前端生产构建通过。
全量测试仍存在两项本轮未改动行为与旧断言不一致：
`tests/test_intent.py::test_low_confidence_keeps_llm_fallback`、
`tests/test_query_rewrite.py::test_no_previous_context_calls_llm`。
`npm test` 当前没有找到前端测试文件，返回非零退出码。
没有对真实 LLM、Redis 多进程故障或线上数据执行端到端验收。
