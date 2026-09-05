# AI Customer

企业智能客服与工单自动处理系统，包含用户认证、意图路由、RAG 检索、工具调用、工单流转和可观测性能力。

## Docker 部署

项目只提供一套 Docker 部署：完整依赖栈，包括 MySQL、Redis、Milvus、etcd、MinIO 和应用服务。Compose 配置位于 [docker-compose.yml](<C:/Users/lz/Desktop/简历&项目/项目一/AI Customer/docker-compose.yml>)，不再使用 profiles；执行 `docker compose up` 即可启动整套服务。

### 1. 准备环境变量

```powershell
Copy-Item .env.example .env
notepad .env
```

请修改 `.env` 中的数据库密码、MinIO 密码和 `AUTH_SECRET_KEY`。生产环境还应填写 `LLM_API_BASE`、`LLM_API_KEY`、`EMBEDDING_API_BASE`、`EMBEDDING_API_KEY` 与模型名称。`.env` 不得提交到版本库。

### 2. 启动依赖与应用

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f app
```

首次部署或升级数据库结构时，执行迁移：

```powershell
docker compose run --rm app alembic upgrade head
```

应用地址：<http://localhost:8000/>，OpenAPI 文档：<http://localhost:8000/docs>，健康检查：<http://localhost:8000/health>。

### 3. 停止与数据

```powershell
docker compose down
```

MySQL、Redis、Milvus、etcd 和 MinIO 数据保存在 Docker named volumes 中。`docker compose down -v` 会删除这些数据卷，请仅在确认需要清空数据时使用。

## 配置与安全

- `ENVIRONMENT` 默认是 `integration`，`DEBUG=false`，`USE_MILVUS=true`，`REDIS_ENABLED=true`。
- 生产环境必须使用随机的 `AUTH_SECRET_KEY`（至少 32 字符），并保持 `AUTH_DEMO_ACCOUNTS_ENABLED=false`。
- 配置明确的 `CORS_ALLOW_ORIGINS`，不要使用 `*` 配合 credentials。
- 外部服务不可用时，应用会通过 `/health` 暴露原因，并按现有适配器降级；这不替代对 MySQL、Redis、Milvus 和 LLM 的可用性监控。

## 验证与测试

```powershell
python -m compileall app scripts
python -m pytest -q
```

CI 脚本位于 `scripts/ci.ps1`，Docker 镜像构建使用 [Dockerfile](<C:/Users/lz/Desktop/简历&项目/项目一/AI Customer/Dockerfile>)。

## 相关文档

- [部署与配置](<C:/Users/lz/Desktop/简历&项目/项目一/AI Customer/docs/DEPLOYMENT.md>)
- [API](<C:/Users/lz/Desktop/简历&项目/项目一/AI Customer/docs/API.md>)
- [演示脚本](<C:/Users/lz/Desktop/简历&项目/项目一/AI Customer/docs/DEMO.md>)
- [RAG 可靠性](<C:/Users/lz/Desktop/简历&项目/项目一/AI Customer/docs/RAG_RELIABILITY.md>)
- [可观测性](<C:/Users/lz/Desktop/简历&项目/项目一/AI Customer/docs/OBSERVABILITY.md>)
