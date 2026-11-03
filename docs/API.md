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

前端工单表单：同参数失败重试沿用键；修改提交参数后生成新键。网络结果未知时，应先核查原工单是否已创建；更换键不能保证跨请求意图去重。聊天创建工单已在M1-06接入本服务，内存人工协同仍未统一，不属于上述保证范围。

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

## 兼容与弃用

旧页面仍可使用 `/login.html`、`/user.html`、`/admin.html`。页面内部统一调用 `/api/v1`，新增客户端请勿依赖未带版本前缀的路径。
