# AI Customer

## AI 应用开发项目说明

这是一个面向 AI 应用开发岗位的可运行客服 Agent Demo，重点展示单体应用内的模型编排与业务闭环，而不是企业级微服务拆分。

核心链路：

```text
用户消息 → 规则优先意图识别 → 低置信度时 LLM 结构化兜底
→ 槽位填充 → RAG 检索或业务工具调用
→ 退款确认/人工转接 → LLM 生成 → 答案校验 → 流式输出
```

当前实现的重点：

- 规则优先、LLM 兜底的意图识别，并记录 `intent_source`。
- 技术/活动咨询采用 BM25 + 向量召回 + 合并重排，提供离线评测脚本和样例集，可输出 Recall@K、Top-1 准确率、MRR、Precision@K 和 P50/P95 延迟：`python scripts/rag_benchmark.py --dataset scripts/rag_eval_dataset.json`。
- 退款操作使用 LangGraph interrupt，明确确认后才执行，支持幂等键和取消路径。
- REST 与 WebSocket 入口共用 `app/services/chat_service.py` 的状态恢复与 Agent 执行逻辑，流式入口同时复用统一的结果评测、Trace 和落库服务。
- 核心链路测试位于 `tests/test_ai_application_optimizations.py`，覆盖 LLM 意图兜底、会话上下文恢复和退款取消。

项目包含用户认证、意图路由、RAG 检索、工具调用、工单流转和可观测性能力，重点用于展示 AI 应用开发中的 Agent 编排与业务闭环。

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
