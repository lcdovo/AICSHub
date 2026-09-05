# 部署与运行手册

## Profiles

| profile | 服务 | 数据与模型 | 用途 |
| --- | --- | --- | --- |
| `local` | `app-local` | SQLite、内存检索、Mock LLM | 面试演示、离线回归 |
| `integration` | `app-integration`、MySQL、Redis、Milvus（etcd/minio） | 外部数据库、向量服务、可选真实 LLM | 联调与预生产 |

Compose 为每个服务配置 healthcheck、持久化卷和依赖启动顺序。应用镜像使用 UID/GID 10001 的 `appuser`，生产环境 `DEBUG=false` 且关闭演示账号。不要把 `.env` 或 `.env.integration` 提交到仓库。

## 数据库迁移

生产结构由 Alembic 管理：

```powershell
alembic upgrade head
alembic downgrade -1  # 仅在明确回滚窗口执行
```

local 首次启动允许 SQLite `create_all` 便于演示；升级结构仍应先生成并审查 migration。数据库文件挂载在 Compose 的 `local_data` 卷，重启不会丢失会话、工单、评测和知识库。

## 健康与就绪

- `/health`：返回 `healthy/unhealthy`、运行模式、数据库/Redis/Milvus/LLM 状态、延迟和降级原因。
- `/metrics`：管理员专用 Prometheus 文本出口；标签只使用意图、工具和后端等白名单值，不包含用户原文或 Token。
- Docker healthcheck 仅在 `/health` 可访问时判定容器 ready；数据库不健康时应用仍保留进程以输出诊断。

## 安全上线清单

1. 设置随机 `AUTH_SECRET_KEY`（至少 32 字符），关闭 `AUTH_DEMO_ACCOUNTS_ENABLED`。
2. 设置明确的 `CORS_ALLOW_ORIGINS`，禁止 `*` + credentials。
3. 配置 MySQL/Redis/Milvus/LLM 凭据到密钥管理系统，不写入镜像或日志。
4. 先执行 `alembic upgrade head`，再启动应用；集成测试通过后切换流量。
5. 为 LLM 配置 Token/延迟预算和超时；不可用时确认规则/Mock 降级仍可用。
