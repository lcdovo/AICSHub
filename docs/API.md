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

## 显式会话创建与状态快照（M1-03）

`POST /api/v1/sessions/`（JSON `user_id`）与 `POST /api/v1/chat/sessions?user_id=...` 共用创建服务，保留各自原有响应字段。普通用户只能为自己创建，管理员可显式为现有目标用户代建。数据库`Session.user_id`始终为目标owner；同一事务的`create_session`操作审计记录可信actor和owner。目标不存在返回404，普通用户伪造owner返回403。此契约不额外更改目标账号的启用/禁用业务规则。

数据库提交会话与审计后，通过`SessionManager`以`SET NX EX`初始化`session:{id}`对象快照，包含整数`schema_version=1`、session_id、user_id和空历史等状态字段；不再把整数用户ID写入同一状态键。NX防止迟到初始化覆盖已经发生的聊天。Redis初始化不可用时保留已提交会话并返回创建成功；后续聊天继续按现有共享锁/单进程降级策略处理，不代表Redis故障下支持安全多worker写入。

读取兼容已有合法的无schema_version对象，但拒绝整数/数组、未知版本、非法身份或session_id与键不匹配的快照。异常共享快照返回明确会话状态失败（聊天当前映射503），不删除、不覆盖、不猜测为无历史。恢复聊天前还会验证快照owner与本轮user_id一致。历史整数值、缓存丢失后的数据库历史重建与待确认动作恢复尚未实现；不要用清缓存作为常规修复，也不要将本切片视为AC-05-03/04恢复验收通过。

此次不改数据库schema、不迁移历史会话；自动创建会话的REST/WS聊天入口保留原逻辑，首轮持久化通过相同序列化器保存对象快照。新增actor审计保证仅覆盖上述两个显式创建入口。关闭/删除竞态、WS身份撤销和完整恢复仍按后续切片验收。

## 工单与转人工创建的幂等契约（M1-02）

`POST /api/v1/tickets` 与 `POST /api/v1/handoff` 共用原子创建服务。客户端可提供请求头 `Idempotency-Key` 或请求体 `idempotency_key`；同时提供时须完全一致。键长度为1至128字符，不允许空值或全空白；不裁剪键的前后空白。无键时每次请求独立创建，不提供请求去重保证。

幂等作用域包含数据库认证主体（actor）、目标资源所有者（owner）、操作类型与客户端键。普通用户不可伪造owner；管理员代建的审计同时记录actor和owner。不同用户、不同操作类型可以使用相同新键，各自得到自己的记录。

服务端保存包含默认值的原始业务参数摘要，不把可变的工单当前状态当作请求摘要。工单摘要包含owner、category、content、priority、session_id；转人工包含owner、session_id、reason、priority、context（省略/null统一为空对象，JSON对象字段顺序不影响摘要）。相同作用域、相同参数返回原资源ID及当前状态，不重复入队通知或审计；改参数返回409。提供session_id时，新建记录校验会话属于目标owner。资源已删除或回执异常时返回409，不重建原动作。

工单/转人工资源、通知记录、操作审计在同一个数据库事务提交；审计键的数据库唯一约束裁决并发请求，竞争失败者回滚后读取胜者回执。没有进程内幂等字典或单进程锁依赖。通知入队不代表通知已经发送，转人工请求创建不代表已有座席接单。

旧版原始键没有可信actor与请求摘要：命中旧记录返回409并要求核查原请求，保留所有历史数据；不要换新键盲重试已可能成功的旧动作。此次无需schema迁移，但要求既有`operation_audits.idempotency_key`唯一约束存在（模型及原迁移已有该约束）；真实旧MySQL schema未在本轮核验。升级时应切换全部写入worker，不能假定新旧协议混跑具备同一幂等语义；本轮未部署。

前端工单表单：同参数失败重试沿用键；修改提交参数后生成新键。网络结果未知时，应先核查原工单是否已创建；更换键不能保证跨请求意图去重。聊天工具及内存人工协同尚未统一到本创建服务，不属于上述保证范围。

## 兼容与弃用

旧页面仍可使用 `/login.html`、`/user.html`、`/admin.html`。页面内部统一调用 `/api/v1`，新增客户端请勿依赖未带版本前缀的路径。
