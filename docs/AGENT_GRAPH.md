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

创建工单工具关闭进程内成功缓存且只自动尝试一次；同一源消息的MCP响应丢失只查询数据库唯一键和请求摘要重读回执，不再次执行写操作。M2现通过client_message_id与SQL Run补齐客户端重试去重和回执恢复，见下文。

M1-07的`update_ticket_status`与管理员HTTP更新共用事务、既有状态机与审计。工具需要独立的update_ticket_status用途内部凭据，执行时重查管理员角色；没有普通聊天自动选用或签发更新凭据的新路径。更新采用原字段条件写入、持久键与操作回执，依赖故障不构造“已更新”；note保存为审计备注，不替代resolution。同键重放返回原提交快照，最新状态另查详情。工具只自动尝试一次，不使用进程内成功缓存；不能据此宣称完整并发/恢复验收通过。

M1-08的`send_notification`在没有实际投递适配器时明确返回channel_unavailable，不生成sent回执、不写库、不使用旧成功缓存。当前三类渠道均未接入；已有NotificationRecord(PENDING)只是工单/人工请求事务写入的待处理事件，M2新增显式启动的消费worker；默认无渠道时failed，--simulate生成标记为模拟的独立回执，不能表述为真实发送。

M1-09的人工节点使用本轮来源消息/actor签发request_handoff用途凭据，工具与HTTP入口共用持久人工请求、通知事件与审计事务。成功仅代表请求已登记，未接通客服；失败或回执无效不再替换为“预计等待3–5分钟”等虚构话术。节点保留handoff_attempted终止本轮，避免校验失败再次转人工的循环，凭据不写入工具历史。回复模板仅使用请求ID及数据库状态。内存协同服务不再承接此创建路径，旧内存座席/统计、分配负载及完整恢复仍未验收。

M1固定收尾R1/R2：`apply_refund`绑定SQL持久action_id/version/参数/金额/期限和本轮确认。`ChatService`每轮核对持久动作，快照缺失时重建有限消息历史；M2在此基础上新增SQL Run与回执恢复。预览只写动作，取消持久化；多动作必须指定目标。提交采用订单级唯一事务闸门与当前读实现跨会话业务去重，审核与取消使用同一闸门。全额CNY、签收后7天、缺失签收不批准，审核始终为模拟未支付。已提交action返回登记时原回执，最新审核结果另查。WS的stream_end在持久化及释放锁后发布。

R3/R4：MCP写调用派发后结果未知只读SQL回执，未知保持outcome_unknown；只有派发前确定未执行才可直调回退。私有订单/历史查询同样验证绑定actor与参数的短期内部凭据、实时账号角色；裸user_id不能授权。状态、工具数据与文档中的注入不能签发执行凭据；Trace、评测副本和结构化日志脱敏，业务事实校验仍用本轮原始证据。

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

REST与WebSocket通过同一 `run_execution` 执行器接受/恢复/执行/落库。Run由owner/session/client_message_id唯一约束去重；消息和事件以SQL为事实源。独立执行任务不随socket断开取消，最终消息/Run终态/最终事件/摘要Job同事务提交后发布。

Redis锁保持令牌/续租/比较释放；SQL会话租约45秒、每10秒续期，条件写入检查版本、状态和所有权。Redis获取前不可用由SQL后备协调；获取后失租停止提交。工单/人工/退款工具在业务事务内检查源消息Run、SQL fence与已取得的Redis令牌。真实MySQL双进程在有/无Redis下各20轮通过；不扩展到任意未接Run入口。

孤儿扫描只读原业务回执并通过响应节点独立校验后补齐最终文本，不自动重放图/模型/未知写。无证明结果明确failed/interrupted_unknown。关闭/删除递增版本、tombstone阻止迟到worker复活；审计与业务回执保留。SQL摘要游标与rules-v1由独立Job推进；Redis仅作带版本CAS的投影。完整契约见 `M2-IMPLEMENTATION-v1.md`。

LLM 调用元数据使用 ContextVar 隔离，并随 Runnable 返回值显式传回，避免不同用户的
并发调用串用模型、费用或自检结果。

## 回归验证

`tests/test_chat_turns.py` 覆盖跨轮状态清理、缓存隔离与失效、最终稿缓存、拒答不外发、
重生成复核、退款确认跨传输、JSON 快照、并发消息顺序、锁超时、租约丢失和模型元数据隔离。
API 回归使用临时 SQLite 数据库，LLM/MCP/Redis 使用可控替身，不触碰本地业务数据。

## 历史轮次记录（以下不是M2当前验收结论）

M2最终结果及真实依赖/替身边界见 `M2-EVIDENCE-v1.md`。

### 当时改动文件

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


## M3知识事实与RAG正确性

生产知识路径现为稳定SQL文档/不可变版本→持久knowledge_index Job→严格batch向量→指纹命名新collection→SQL原子发布指针/epoch。旧IngestionMixin/IndexAdminMixin仅兼容转发，移除手写DDL及按标题删除旧旁路。

每请求独立取SQL发布快照，真实Milvus dense与SQL BM25独立执行并应用层RRF；Embedding异常仍返回关键词降级。recall/rerank/evidence数量独立；score_type保留，RRF不与余弦阈值混比。完整有界chunk与FAQ/表头上下文进入生成/Judge，不再按300/200字前缀裁剪；证据覆盖校准仍属M5，EvidenceBundle.support保持unknown。缓存按epoch/模型/策略/权限范围隔离，生成及校验前复核活动版本。

M3实现、逐项21项范围、隔离真实依赖证据与供应商额度阻塞见M3-CLOSEOUT-v1.md、M3-IMPLEMENTATION-v1.md、M3-EVIDENCE-v1.md；不能以合成向量证明真实模型效果或生产部署完成。

## M4候选理解边界（2026-11-04）

Run固定rules/shadow/model与策略版本。model对非确定问候执行RequestUnderstanding，严格schema和来源验证、无效至多修复一次；咨询送检索，条件先订单查询，不直接准备写动作，失败澄清。shadow在确认/取消前拦截，本轮不写。新理解查询复用到RAG不重复改写；Context/ActionIntent/EvidenceBundle/ReviewResult每轮清理，图最大24步。只读双任务计划已接主图；多诉求中的业务动作仍需独立确认，完整任务成功率未验收。当前业务rules/灰度0，原因是独立留出实体95.67%低于97%；不要把候选代码存在当作已激活或M4通过。

影子响应在完整图内固定为可信流程通知，不生成/缓存/发布供应商草稿，不进入回答审查失败后的人工转接写分支；它不是降低普通或model回答的独立审查标准。已接受Run模式/版本固定，配置回滚只影响新Run，相同请求回放不重做动作。

主图模型query输出可选read_tasks：最多2项query_order/get_user_history。工具节点从本轮可信actor/user/session构造ReadPrincipal，重新验证订单实体来源，经原私有签名/实时权限调用工具。独立读取并行，失败或父Run取消会取消并回收同组任务；结果逐项报告不隐式执行条件写动作。读结果校验失败澄清，不创建人工请求。
