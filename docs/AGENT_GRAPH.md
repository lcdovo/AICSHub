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

SQL恢复时不信任会话投影中的refund_preview/refund_action/confirm_refund：清除旧动作与确认，从未过期pending SQL动作重建待确认。终态显式action_id可供领域服务核验/幂等回执但不标pending；旧checkpoint不能复活取消动作。

Run和模型不能仅依赖asyncio.timeout抛异常：依赖若吞取消或阻塞，返回后仍检查单调deadline，流在yield前检查；Run持久化与commit前再次检查。迟到返回不发布，已提交业务按回执恢复而不重跑，不能把线程无法强杀解释为允许迟到写入。

当前证据主链通过AgentState.accept_retrieval统一核验results/EvidenceBundle和epoch，严格schema失败或检索失败不保留可生成结果。current_evidence返回独立类型快照；旧kb_results只用于无bundle的兼容内部调用。失效统一清类型/普通/工具证据与旧审查；ReviewResult及回答缓存绑定完整证据fingerprint，生成中失效提示不缓存。检索support不替代独立证据审查。整图collected_info与self依赖重构仍未完成。

模型路径的order_id在RequestUnderstanding来源校验后固定，route_decision/tool_execution旧regex不能改回首个文字订单。apply/query/change未确定订单时不复用旧槽位；rules路径仍用原提取契约。否定词按退款/投诉/人工各自topic作用，不把“不要退款”当作否定明确的人工请求。

查询改写/检索已注册为QueryRewriteNode和RetrievalNode，分别注入rewriter、knowledge_factory/top_k/minimum_score；核心节点不反向依赖graph模块。旧私有节点函数仅兼容适配器。其余节点依赖迁移进度见M4-NODE-DEPENDENCIES-v1.md，不把局部迁移当整图完成。

工具执行/确认分别注册ToolExecutionNode、ConfirmationNode并显式注入公共策略、调用器及RefundActionCoordinator；有效待确认动作计数与预览事务位于领域接口。结果核验独立读取AgentState。旧私有方法仍仅兼容调用；其余节点迁移未完成。

意图/路由/澄清注册显式依赖节点，补槽为state函数。当前确认/取消快路径不解析模型工厂，影子仍先拦截写入。纯策略不依赖图私有助手，旧方法兼容层保留；响应组件迁移尚未完成。

当前13节点主图装配已收口：ResponseNode注入模型/Validator/Judge/缓存/快照/记忆六类服务，响应助手留在组件内部；HandoffNode注入授权与调用，开始/结束是状态函数。Graph旧私有别名仅兼容直调。流式通道独立于Graph，拒绝草稿仍不发布。完整模块与节点清单以M4-NODE-DEPENDENCIES-v1.md当前表为准；早期迁移进度段落为历史说明。collected_info状态键全面类型化仍未完成。

业务工具/只读计划/人工回执通过record_tool_observation进入ToolObservation，data不再展开到collected_info控制键；只投影明确允许的业务编号，完整回执与ToolCall独立复制。最新失败不留该工具旧成功投影，下一轮清空观察。完整状态类型迁移进度见M4-STATE-CONTRACT-v1.md，尚未完成。

本轮smalltalk/grounded/refused/validated/cache-hit/handoff-attempted六项流程结论现由严格TurnFlow和FlowFlag管理，None/False/True分开。旧字典仅兼容镜像，已有或显式清空结论不能被字典复活；新轮次清空。其他状态/计量/槽位类型迁移仍见M4-STATE-CONTRACT-v1.md未完成范围。

生成计量头现在由GenerationMetadata/ProviderTokenUsage选择并验证，完整回答和输入摘要不入llm_governance兼容投影。unknown/partial usage不补缺失输出为0；无效头明确degraded、禁止缓存，回答仍独立审查。评测/Trace读取类型模型版本，SQL调用账本是费用事实。更广泛槽位类型化仍未完成。

SQL会话恢复与内存新轮次使用同一carryover_context白名单，不能因历史SQL投影被视为“持久”就恢复旧证据/校验/计量/工具结果。当前退款动作仍独立从SQL核验，补参背景保留；不删除旧历史投影。

订单/退款原因/退款类型/投诉内容统一BusinessParameters类型输入，路由/工具提取只接受声明字段；补槽/改写/known_order/提示词/跨轮上下文使用当前类型参数。已选或明确清空订单不能被旧字典改写。身份与确认不在业务参数模型内；领域资格/幂等/确认和原补槽策略保持，其他状态迁移仍未完成。

退款节点确认来源收口为本轮精确确认词或绑定本轮身份/消息/完整预览的TurnRefundConfirmation；旧confirm_refund bool只是镜像，变化后的暂停标记与历史确认不能复用。真实提交仍检查SQL当前用户消息及Action精确参数，不把类型观察当权限令牌。

RefundPreview提供预览统一类型入口：归属/形状验证，镜像或当前scope变化拒绝、同轮确认失效，SQL恢复建立新快照；节点和Run关联不直接使用裸预览。跨轮兼容导入在副本中进行，旧状态不被改写。已提交回执恢复能处理损坏旧预览，不重做退款；金额/状态显示等剩余状态类型化仍见M4-STATE-CONTRACT-v1.md。

动作金额/状态展示现在读取严格RefundActionView，必须完整且匹配当前预览、pending未过期后才显示确认。错误/缺失prepare回执与镜像变化明确拒绝，不用裸字典索引崩溃，不补造金额；提交/取消清本轮视图但保留SQL事实。领域SQL确认和幂等仍独立强制。

人工转接建议与请求事实分开：need_human只表示建议/诉求。响应无现成回复时优先使用同用户、同会话且状态有效的成功工具回执；仅建议时明确尚未创建，尝试失败时明确结果无法确认，投诉工单真实回执不被转接话术覆盖。人工计数还需查询SQL中同scope的HandoffRequestRecord，不能仅凭need_human或工具声称成功计数。此处是进程内Trace指标，不等于跨worker持久统计或全部workflow安全审查已完成。

M5新增所有文本共用的publication_guard：workflow、模板、缓存、生成与服务端引用均在token/回答缓存发布前检查已识别敏感值和未支持的接通/排队承诺。失败发布固定安全说明且不创建新动作。此确定性底线不替代政策Judge、全部事实审查或人工质量验收；范围见M5-EVIDENCE-v1.md。

M5政策Judge关闭/不可用/非法/异常/超时均unknown、不得作为放行，未grounded不因高分通过。生成与Judge共用完整format_review_evidence视口，保留每片段ID/版本与后部条件及成功工具完整回执。缓存使用validated-v3隔离旧宽松审查结果；实际模型新schema兼容/质量仍待验。

M5固定业务核验读取全部相关成功回执，状态反转、订单金额/币种矛盾、同订单冲突、无申请回执登记及真实支付承诺critical阻断。最终workflow出口同样检查；长度/敏感名词不等于事实错误。固定规则覆盖及语义限制见M5-EVIDENCE-v1.md，不能据此宣称任意承诺/多对象自然语言已完全可靠。

M5检索服务search可显式选择retrieval_mode=hybrid/bm25/dense作消融，缓存隔离选择，默认聊天/API仍hybrid；单路不执行另一召回器。真实本地开发消融采用实验进程30秒预算，不等同业务默认5秒重排或独立留出验收。

后续本地BGE重排按4条/批、每进程共享最多2批执行，完整评分全池后才取top_k；父检索绝对截止和HTTP剩余预算贯穿各批/后备provider，晚到结果拒绝，失败保留融合排名。候选超过支持24条整次回退，不静默丢尾部。外部及其他本地模型不拆请求，缓存/meta含算法与预算。执行器现在默认复验5秒，30秒实验需显式指定；真实默认实验8/12local、4超时，P95未达2秒，见M5-RERANK-BUDGET-v1.md。

本地Embedding和重排HTTP出口现共用单进程active1/waiters2预算，前台等待500ms、后台50ms，父阶段deadline通过线程context传播；排队时间不重置阶段截止。后台索引遇忙/截止进入有界Job退避并保留旧发布指针。瞬时busy/deadline检索不缓存，证据不足时返回真实繁忙/超时提示，不创建写入型人工请求。此预算不覆盖独立worker进程/其他模型客户端，也不能强杀服务端已发推理；完整作用域、真实拒绝率及未通过门槛见LOCAL-INFERENCE-BUDGET-v1.md。

后续冷检索/后台本地Embedding批次改为整轮Permit，已获资格的模型HTTP不再各自排队。可选Redis原子owner/等待ticket/TTL/续租与条件释放，使同Redis/namespace的已升级API/Job worker协作；默认process未切换。外层结束后未完HTTP仍持有资格到返回，晚到结果拒绝；失租不发后续请求，同token重放幂等、未知申请回包只条件清理自己的占位。1038项T0和7项真实Redis场景通过，但真实GPU压力FAIL，不能启用业务或宣称完整跨系统GPU互斥，见WHOLE-REQUEST-ADMISSION-v1.md。

后续v2在HTTP引用归零后对异常释放最多补偿一次，每次仍比较同owner；`released`/`not_owner`/`unknown`与尝试次数显式进入scope观测，连续失败依TTL回收，不能将尝试过视为删除成功。前/后台ticket分别条件清理。135项受影响回归与11项真实Redis场景通过，非全量/容量验收；详见LOCAL-LEASE-RELEASE-v1.md。
