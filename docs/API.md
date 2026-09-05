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
curl -s http://localhost:8000/api/v1/users/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"user001","password":"user12345"}'

# 使用返回的 access_token 聊天
curl -s http://localhost:8000/api/v1/chat/send \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":1,"message":"请查询订单 ORD20240001"}'
```

管理员可先调用 `POST /api/v1/knowledge/documents/seed` 导入演示资料，再调用 `POST /api/v1/knowledge/rag-query` 验证检索与引用。

## 错误码

| HTTP | `code`/场景 | 处理建议 |
| --- | --- | --- |
| 400 | 参数格式、状态转换或业务校验失败 | 修正请求后重试 |
| 401 | 缺少/过期/撤销 Token | 重新登录或刷新 Token |
| 403 | 角色不足或访问他人资源 | 不重试，检查资源归属 |
| 404 | 资源不存在 | 刷新列表或提示用户 |
| 409 | 幂等键冲突/资源状态冲突 | 使用原幂等键查询结果 |
| 413 | 文件或消息超过限制 | 压缩内容或拆分请求 |
| 429 | 登录/聊天限流 | 按 `Retry-After` 退避 |
| 500 | 未预期服务错误 | 携带 `X-Trace-Id` 联系运维 |

## 兼容与弃用

旧页面仍可使用 `/login.html`、`/user.html`、`/admin.html`。页面内部统一调用 `/api/v1`，新增客户端请勿依赖未带版本前缀的路径。
