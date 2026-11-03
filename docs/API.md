# API 交付说明

## 版本与访问入口

业务 API 当前版本为 `/api/v1`，版本升级时保留旧版本兼容窗口。OpenAPI 规范由 FastAPI 自动生成：

- Swagger UI：`GET /docs`
- ReDoc：`GET /redoc`
- 原始规范：`GET /openapi.json`

所有响应都使用 `code`、`message`、`data` 三段式结构（健康检查和 Prometheus 文本出口除外）。请求链路会返回 `X-Trace-Id`，可用该值查询管理员接口 `/api/v1/analytics/traces/{trace_id}`。

## 认证与权限

登录 `POST /api/v1/users/login`，将返回短时 `access_token` 与可轮换的 `refresh_token`。需要登录的请求带上：

```http
Authorization: Bearer <access_token>
```

| 角色 | 可访问范围 |
| --- | --- |
| `user` | 自己的会话、消息、工单、RAG 搜索和聊天；只能以自己的 `user_id` 发起请求 |
| `admin` | 用户管理、知识库写入/发布/重建、全量工单/转人工、评测反馈、`/metrics` |

服务端从数据库读取角色，客户端传入的 `role` 不参与授权。越权返回 `403`，未登录或 Token 无效返回 `401`。

### REST / WebSocket 身份有效期（M1-04）

Access Token必须包含`exp`、`sub`、`type`、`ver`，使用HS256签名；`sub`是正整数ID的ASCII数字字符串，`ver`是非负整数（布尔值无效），`type=access`。REST与WS共用数据库身份校验，拒绝不存在、status关闭、disabled_at非空、token_version不匹配的账号；权限使用数据库当前role。

WS保持`Authorization: Bearer ...`或现有`access_token`查询参数的兼容入口，Bearer scheme大小写不敏感。握手时身份失效关闭4401；连接建立后每条消息先重新认证，失效发送`type=error, code=401`并关闭4401。REST聊天与WS在等待会话锁后、写用户消息/运行图之前再校验凭据及资源权限。等待期间撤销可阻断下一步消息和图执行；不保证取消已经开始的工具或撤回已提交动作。

注销递增`token_version`会使该用户此前所有版本的Access及Refresh Token失效。刷新除检查存储摘要、过期和撤销记录外，也检查当前账号状态与版本；顺序轮换后的旧refresh不可复用。本轮未解决并发刷新竞争。降级管理员使用旧Token不能继续访问管理员HTTP接口，但仍可在当前角色允许的范围使用本人服务。WS继续只允许本人user_id和会话，不增加管理员代聊能力。

认证数据库不可用时不执行图或回退示例身份。WS握手/逐消息认证的数据库异常关闭1013（已连接时先报503）；后续执行阶段的非业务异常使用脱敏500后关闭。本轮未实现短期WS票据/Cookie迁移，也未验证反向代理对握手拒绝和关闭码的实际处理；查询参数Token的代理日志风险仍待独立处理。

## 最小 OpenAPI 示例

```bash
# 登录（本地演示账号）
curl -s http://localhost:8100/api/v1/users/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"user001","password":"user12345"}'

# 使用返回的 access_token 聊天
curl -s http://localhost:8100/api/v1/chat/send \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":1,"message":"请查询订单 ORD20240001"}'
```

管理员可先调用 `POST /api/v1/knowledge/documents/seed` 导入内置知识资料，再调用 `POST /api/v1/knowledge/rag-query` 验证检索与引用。

## 错误码

| HTTP | `code`/场景 | 处理建议 |
| --- | --- | --- |
| 400 | 参数格式、状态转换或业务校验失败 | 修正请求后重试 |
| 401 | 缺少/过期/撤销 Token | 重新登录或刷新 Token |
| 403 | 角色不足或访问他人资源 | 不重试，检查资源归属 |
| 404 | 资源不存在 | 刷新列表或提示用户 |
| 409 | 幂等键冲突/资源状态冲突 | 核查原请求；勿用已变更参数反复重试旧键 |
| 413 | 文件或消息超过限制 | 压缩内容或拆分请求 |
| 429 | 登录/聊天限流 | 按 `Retry-After` 退避 |
| 500 | 未预期服务错误 | 携带 `X-Trace-Id` 联系运维 |

## 关闭会话的聊天准入（M1-05）

两种现有关闭入口`PUT /api/v1/sessions/{id}/close`和`PATCH /api/v1/chat/sessions/{id}/close`提交关闭后，REST聊天返回400、detail为“会话已关闭”；WS收到该会话的新消息时返回`type=error, code=400, message=会话已关闭`，不发布stream_start/token/done/stream_end，不自动重开。WS保留连接，可显式创建另一会话后继续使用。没有新增重开接口。

REST/WS均在取得会话锁并刷新数据库记录后检查关闭状态，因此聊天请求等待锁期间已提交的关闭也会拒绝。被拒绝的聊天不增加消息、轮次Trace/评测、消息计数，不覆盖closed_at或清理原会话快照/待确认参数。普通用户请求他人关闭会话时先按权限返回403，不能借关闭提示探测其状态。

此准入保证不等于运行中取消：关闭入口本轮仍未与聊天共用会话锁，关闭发生在最后检查之后可能仍有已开始的轮次完成。删除竞态、跨worker CAS/tombstone、动作中途取消与回执恢复尚未完成（AC-15-10），不宣称完整关闭/恢复原子性。

## 显式会话创建与状态快照（M1-03）

`POST /api/v1/sessions/`（JSON `user_id`）与 `POST /api/v1/chat/sessions?user_id=...` 共用创建服务，保留各自原有响应字段。普通用户只能为自己创建，管理员可显式为现有目标用户代建。数据库`Session.user_id`始终为目标owner；同一事务的`create_session`操作审计记录可信actor和owner。目标不存在返回404，普通用户伪造owner返回403。此契约不额外更改目标账号的启用/禁用业务规则。

数据库提交会话与审计后，通过`SessionManager`以`SET NX EX`初始化`session:{id}`对象快照，包含整数`schema_version=1`、session_id、user_id和空历史等状态字段；不再把整数用户ID写入同一状态键。NX防止迟到初始化覆盖已经发生的聊天。Redis初始化不可用时保留已提交会话并返回创建成功；后续聊天继续按现有共享锁/单进程降级策略处理，不代表Redis故障下支持安全多worker写入。

读取兼容已有合法的无schema_version对象，但拒绝整数/数组、未知版本、非法身份或session_id与键不匹配的快照。异常共享快照返回明确会话状态失败（聊天当前映射503），不删除、不覆盖、不猜测为无历史。恢复聊天前还会验证快照owner与本轮user_id一致。历史整数值、缓存丢失后的数据库历史重建与待确认动作恢复尚未实现；不要用清缓存作为常规修复，也不要将本切片视为AC-05-03/04恢复验收通过。

M1-03不改数据库schema、不迁移历史会话；自动创建会话的REST/WS聊天入口保留原逻辑，首轮持久化通过相同序列化器保存对象快照。新增actor审计保证仅覆盖上述两个显式创建入口。WS身份撤销见M1-04，关闭准入见M1-05；删除竞态与完整恢复仍待后续验收。

## 工单与转人工创建的幂等契约（M1-02）

`POST /api/v1/tickets` 与 `POST /api/v1/handoff` 共用原子创建服务。客户端可提供请求头 `Idempotency-Key` 或请求体 `idempotency_key`；同时提供时须完全一致。键长度为1至128字符，不允许空值或全空白；不裁剪键的前后空白。无键时每次请求独立创建，不提供请求去重保证。

幂等作用域包含数据库认证主体（actor）、目标资源所有者（owner）、操作类型与客户端键。普通用户不可伪造owner；管理员代建的审计同时记录actor和owner。不同用户、不同操作类型可以使用相同新键，各自得到自己的记录。

服务端保存包含默认值的原始业务参数摘要，不把可变的工单当前状态当作请求摘要。工单摘要包含owner、category、content、priority、session_id；转人工包含owner、session_id、reason、priority、context（省略/null统一为空对象，JSON对象字段顺序不影响摘要）。相同作用域、相同参数返回原资源ID及当前状态，不重复入队通知或审计；改参数返回409。提供session_id时，新建记录校验会话属于目标owner。资源已删除或回执异常时返回409，不重建原动作。

工单/转人工资源、通知记录、操作审计在同一个数据库事务提交；审计键的数据库唯一约束裁决并发请求，竞争失败者回滚后读取胜者回执。没有进程内幂等字典或单进程锁依赖。通知入队不代表通知已经发送，转人工请求创建不代表已有座席接单。

旧版原始键没有可信actor与请求摘要：命中旧记录返回409并要求核查原请求，保留所有历史数据；不要换新键盲重试已可能成功的旧动作。此次无需schema迁移，但要求既有`operation_audits.idempotency_key`唯一约束存在（模型及原迁移已有该约束）；真实旧MySQL schema未在本轮核验。升级时应切换全部写入worker，不能假定新旧协议混跑具备同一幂等语义；本轮未部署。

前端工单表单：同参数失败重试沿用键；修改提交参数后生成新键。网络结果未知时，应先核查原工单是否已创建；更换键不能保证跨请求意图去重。聊天创建工单在M1-06、聊天登记人工请求在M1-09接入本服务。内存座席/协同统计和分配负载仍未统一，不属于上述保证范围。

### 聊天创建工单（M1-06）

REST/WS保存本轮用户消息后，将数据库消息ID和可信actor注入AgentState独立字段；下一轮不继承。工单工具节点以`chat-ticket:{session_id}:{source_message_id}`作为持久幂等键，并签发120秒内部执行凭据，绑定owner、actor、消息ID和完整参数摘要。凭据不会进入工具历史、Trace参数或回复。模型实体/历史中的user_id、session_id或source_message_id不能替代这些字段。

`create_ticket` MCP工具新增session_id、idempotency_key、authorization执行上下文；直调与MCP均验证签名/用途/到期、严格业务参数、当前actor账号状态、会话归属及源消息的USER角色。随后调用与HTTP工单入口相同的创建事务、同一表和回执序列化器。仅传裸user_id的旧MCP调用将明确失败；外部Host没有可信内部凭据时应使用带Access Token的HTTP创建入口，不从模型生成凭据。

成功结果中的工单ID、当前状态、分配与SLA字段来自数据库；未分配时不声称已分配，不承诺虚构处理时限。工具最多执行一次自动尝试，不用进程内成功缓存；提交后回包丢失时，使用原键重查同一记录。失败不会构造工单ID，也不能把依赖异常理解为确定未提交。通知只是入队，未宣称已发送。

此幂等保证只针对同一已落库源消息和相同请求键；客户端重复发送造成新的消息ID时不保证业务意图去重。持久Run/动作确认与崩溃恢复仍待后续。管理员代聊时审计actor为管理员、owner为目标用户；WS继续保持本人会话规则。本轮没有改变投诉进入建单的业务触发条件。

### 工单更新（M1-07）

`PUT /api/v1/tickets/{id}`与`update_ticket_status`工具共用`ticket_update`事务和原有状态机，保持仅管理员可更新。服务层再次读取当前actor状态与角色，普通用户修改本人或他人工单均403；新请求的工单不存在返回404，非法跳转409。API继续支持priority、assigned_to、resolution，混合参数任一无效时不部分写入。

更新与OperationAudit同事务提交。审计保存实际actor、owner、参数摘要、备注及操作提交时的前后状态/工单快照；工具note仅作为审计备注，不隐式改写resolution。使用原状态、可编辑字段和updated_at进行条件更新，竞争冲突409，不静默覆盖另一请求。

API可通过`Idempotency-Key`启用持久重放；作用域包含actor、ticket_id与操作键，同键改参数409。不传键沿用独立请求行为。相同键返回**本次操作的原始提交回执**，不会重新变更时间戳；若工单之后又被更新，最新状态应通过GET详情查询，不将旧回执当成当前状态。

MCP更新工具新增idempotency_key与authorization上下文，凭据限定update_ticket_status用途、120秒有效，并绑定actor和完整参数摘要。普通Access Token、创建工单凭据或裸actor_id不能直接授权MCP更新。内部签发仅供受信管理员适配层，当前普通聊天路由不自动选择更新工具；外部客户端使用已认证管理员HTTP入口。工具不使用进程成功缓存，仅一次自动尝试；依赖错误不意味着确定未提交，应按原键核查。

本轮无schema迁移，依赖现有审计键唯一约束。SQLite确定性竞争与同进程MCP验证不能替代真实MySQL/多worker、stdio、崩溃恢复或完整版本化CAS验收；未新增单调版本列，也不宣称运行中权限撤销能取消已开始动作。

## 通知发送能力边界（M1-08）

当前代码未接入sms/email/inapp的真实投递适配器，也没有消费通知队列的worker。`send_notification`保留工具名及合法调用格式，但三类发送均返回`success=false / error_type=channel_unavailable`；参数非法返回validation。不会生成notification_id、sent状态或sent_at，也不创建“已发送”记录，不自动重试或返回进程内旧成功缓存。MCP工具说明同样标示能力未接入。

工单/转人工创建事务仍可写入`NotificationRecord(PENDING)`，它只代表业务事件待处理，不代表已发送或模拟投递完成；通知工具失败不会修改这些记录。Outbox仓储中的状态写入方法只是数据访问助手，不是可运行的发送worker或投递回执证据。本轮没有接入真实或模拟渠道，没有新增配置项、定时消费或对外通知。

工具对user_id使用严格整数类型（兼容省略时默认0，但不会授权发送），拒绝未知字段/强制发送开关。未来启用发送需独立完成可信身份、持久幂等、投递回执、租约与故障恢复验收，并确认真实服务费用；不能仅加配置或把入队状态改成sent就宣布接通。

## 人工请求登记与查询（M1-09）

聊天的`escalate_to_human`与`POST /api/v1/handoff`共用持久创建事务和回执序列化器，写入HandoffRequestRecord、待处理通知、actor/owner审计。新请求status为pending、assigned_to为空；工具同时返回request_id与兼容别名escalation_id，不生成队列位置、预计等待时间或已接通承诺。原API的优先级/SLA规则不变；sla_deadline是内部响应目标，不是接通等待时间。

新增`GET /api/v1/handoff/requests/{request_id}`，本人或管理员可读取，越权403、不存在404、未认证401。管理员仍通过原`GET /api/v1/handoff/requests`读取列表。当前没有新增用户端界面、实时人工会话或自动座席接单。

工具使用服务端绑定owner、actor、本轮已提交用户消息ID及完整参数hash的120秒凭据，作用域为request_handoff；裸user_id或其他工具凭据不能授权。幂等键为`chat-handoff:{session_id}:{source_message_id}`，不使用进程成功缓存且仅一次自动尝试。原MCP输入context保持字符串，写入共享请求时映射为空对象或`{"context": "..."}`；输出context为数据库对象。普通工具priority仍为normal/urgent，HTTP入口原有high/low不变。缺少内部凭据的外部Host使用已认证HTTP入口。

失败、缺失工具、无效或缺失回执都会返回诚实的未确认结果，图节点不再覆盖成成功；回复模板只描述回执ID及状态。数据库故障可能发生在提交之后，不能把错误理解为一定未登记，需按原键核查。通知入队不表示通知已发送。

新记录不再进入内存CollaborationService的请求字典；历史内存请求未迁移，座席列表和协同统计仍属旧内存路径，不能代表本次持久请求的排队或接通进度。分配/解决接口的座席存在、负载与并发尚未完成验收。本轮不宣称整个人工协同闭环或真实客服系统接入。

## 人工分配准入（M1-10）

`POST /api/v1/handoff/{request_id}/assign?agent_id=...`保持管理员入口，服务层重查操作人启用状态与管理员角色。请求不存在404，非pending请求409；座席不存在404、status不是online或容量已满409，拒绝时不改变请求及审计。成功仍返回原request_id/agent_id，表示内部指派记录已提交，不代表真人已接通。

当前座席名册仍来自原进程内演示HumanAgentService。准入负载取内存current_load与数据库ASSIGNED请求计数的较大值；max_load沿用名册配置。RESOLVED/CANCELLED不计入持久有效指派。`GET /api/v1/agents`保留原字段及skill/status筛选，current_load使用同一口径；不额外增减内存计数，避免持久请求解决后仍保留新产生的内存负载。

分配采用pending及原指派字段条件更新，防止同一请求被并发覆盖；请求与actor/owner/agent审计同事务提交。M1-10时不同请求仍有容量竞争窗口，M1-11新增下述持久闸门。内存与数据库负载仍无法完整去重，不能将较大值视为真实总负载。演示名册不是在线客服系统状态来源，真实多worker验收仍未完成。

本轮不新增座席启用/管理接口，不改原分配后解决与重新分配规则，也不回写/迁移历史指派。旧协同统计仍使用内存数据，不能作为本次持久请求统计；用户界面及浏览器尚未做本轮验收。

### 座席容量事务闸门（M1-11）

新增`handoff_capacity_gates`表，按agent_id主键进行原子upsert，分配事务取得该座席的数据库写锁后再读取ASSIGNED请求，判定容量、更新请求并写审计，直到commit/rollback释放。没有进程内容量锁；SQLite/MySQL分别使用各自upsert，MySQL有效指派使用FOR UPDATE当前读，避免复用旧事务快照。其他数据库类型明确拒绝容量协调。

容量占用直接以当前ASSIGNED记录计算，没有独立可漂移的占用计数器。已有历史ASSIGNED自动参与检查，解决后不再占用，失败事务不留下容量预留；不重写历史指派或增减内存负载。闸门元数据按已知座席首次分配懒创建，不是持久名册/在线状态表。

所有worker必须使用本版本分配路径及一致的名册/容量配置；旧worker或其他直接写指派路径可绕过闸门，不保证混跑安全。SQLite两个独立引擎、20轮各10请求测试通过，仅是离线竞争证据，真实MySQL/双进程/故障恢复验收仍未执行。跨座席重新分配、解决与删除的完整状态机仍待后续。

升级需在切换写流量前执行新增迁移0005_handoff_capacity，迁移只新增闸门表，不种入座席、不改历史指派。本轮仅在临时SQLite验证upgrade/downgrade再upgrade，未对业务库运行。在线回滚不能降回会绕过容量闸门的旧分配实现；应先停止分配写入，不能在活动新worker存在时删除闸门表。

### 人工请求解决的幂等与归属（M1-12）

`POST /api/v1/handoff/{request_id}/resolve?resolution=...&agent_id=...`仍要求管理员，服务层重查当前操作人状态与角色。resolution去除首尾空白后须为1至10000字符；agent_id可省略，提供时只能等于数据库原assigned_to，否则409。agent_id是归属断言，不是重新分配命令；未分配请求不得借解决接口设置新座席。

第一次解决以原状态、指派及结果字段做条件更新，与resolve_handoff审计同事务提交。审计保存真实actor、owner、原座席、原状态、结果和解决时间。有座席时与分配共用持久闸门，直到事务结束才释放；容量继续由ASSIGNED记录计算，无需重复减计数。

已解决请求以同一结果重复调用返回原request_id/resolution，不改变resolved_at或追加重复审计；改结果409，改座席也409。竞争请求若提交同结果可重读同一终态，不同结果只有一个赢家。历史已解决记录可按原结果重读，不伪造补写当年的审计。成功后回包丢失时可重复原请求核查，不能用新结果覆盖。

本轮保持原接口对尚未解决状态的许可范围，包括未分配pending和cancelled；没有将其改为“仅assigned可解决”。是否收紧为新的业务状态规则需要单独确认。解决后不可覆盖处理结果是本轮幂等契约；重新分配、终态纠错接口和完整历史生命周期仍未实现。SQLite故障/竞争检查不替代真实MySQL、多进程或重启验收。

## 模拟退换货动作、登记与审核（M1固定收尾R1/R2）

本节替代M1-13的临时限制描述。流程为持久预览 → 本轮明确确认 → 模拟待审核申请 → 管理员模拟审核；所有状态均不代表真实支付或到账。

- `POST /api/v1/refunds/actions`：严格接收user_id/session_id/order_id/type/reason，可带previous_action_id重新预览；拒绝额外amount。返回action_id/version、订单全额两位小数金额、CNY和UTC expires_at；不创建申请。
- `GET /api/v1/refunds/actions/pending?session_id=...`：本人或管理员查询待确认动作；`POST /actions/{action_id}/cancel`持久取消预览，不能取消已提交申请。
- REST `/api/v1/chat/send`与WS `/api/v1/chat/stream`接收可选action_id/action_version；“确认”必须是本轮USER消息。多个待确认动作未指定目标时澄清；旧版本、到期、已取消或订单/类型/原因/金额不一致时拒绝。历史、文档或模型字段不能授权。
- `GET /api/v1/refunds/`、`GET /api/v1/refunds/{refund_id}`：查询本人申请；管理员可查全量，可用session_id筛选。回执包含两位小数amount、状态、模拟标记和审核信息。
- `POST /api/v1/refunds/{refund_id}/review`：仅当前有效管理员可用。decision为approved/rejected，note必填。审核结果不可覆盖，相同决定/意见可重放；approved明确标“未支付”，写入actor/owner审计。

订单金额为Numeric(12,2)，另有refunded_amount、received_at、currency。资格使用真实订单状态及签收后7天；签收缺失只允许登记待核验，禁止批准；未来/超期时间拒绝。仅支持CNY订单全额模拟申请，剩余额度不足、指定金额或部分退款明确拒绝。待审和已批准申请占用额度，拒绝释放占用，审核本身不更新真实已退款金额。

同用户、订单、类型已有待审申请时，相同业务参数返回原申请，参数变化409。MySQL/SQLite订单级事务闸门覆盖预览、提交、取消与审核；action/version/精确参数和服务端短期凭据共同绑定确认。已提交action重放读取原登记回执，查询接口展示最新审核状态。进程重启或Redis快照缺失可从SQL恢复动作；完成事件在提交并释放会话锁后发布。

`REFUND_ACTION_TTL_SECONDS`默认900秒（允许60～86400），更改只影响新预览。迁移0007新增动作、闸门和签收等字段，先检查旧金额可精确迁移；不猜测历史签收时间、不回填历史伪申请。自动降级拒绝丢失精确金额与动作数据。

## 私有查询与MCP失败契约（R3/R4）

`query_order`与`get_user_history`的user_id只是目标范围，不能作为身份。直接/MCP调用需要服务端签发、绑定工具名/精确参数/actor的短期authorization；每次调用重新读取账号和角色。普通用户只能读取本人，管理员跨用户读取记录owner/actor审计。裸ID、参数篡改、失效账号和降权管理员拒绝；凭据不进入工具历史或Trace。

MCP派发前无法连接可回退进程内同一工具；写调用派发后失去响应只查SQL原回执，无法确定返回outcome_unknown，禁止再次直调。未注册工具、缺少业务回执、参数错误明确失败，不伪造ID或成功话术。当前通知/支付适配器未接入，不表示已发送/已到账。

## 兼容与弃用

旧页面仍可使用 `/login.html`、`/user.html`、`/admin.html`。页面内部统一调用 `/api/v1`，新增客户端请勿依赖未带版本前缀的路径。
