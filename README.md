# AICSHub · 智能客服

基于 FastAPI、LangGraph 和 React 的智能客服系统，面向订单查询、售后咨询、知识问答与人工协同场景，将请求理解、知识检索、业务工具和回答审查整合为统一服务。

## 核心能力

- **对话与任务编排**：支持多轮会话、请求澄清和有界任务循环，可按事项处理复合诉求并记录完成状态。
- **知识检索**：结合 BM25、向量召回与重排，支持知识版本管理、异步索引和发布快照校验。
- **回答审查**：生成草稿经独立 Judge 检查事实、来源与事项覆盖后发布，保留证据绑定和失败拦截。
- **业务处理**：提供订单查询、模拟退款申请、工单管理与转人工；写操作受权限、参数、幂等及本轮确认约束。
- **可靠执行**：REST 与 WebSocket 共用业务链路，结合 SQL 执行账本、会话租约和后台 Worker 管理执行与恢复。
- **可观测与治理**：提供链路追踪、调用计量、预算控制和敏感信息脱敏，支持隔离回归与版本化验收。

任务循环、多意图逐项处理和 Judge 协议按配置启用；仓库模板采用保守默认值，具体开关见 [.env.example](.env.example)。退款流程为模拟申请，不接入真实支付。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 前端 | React、TypeScript、Vite、Tailwind CSS |
| 后端与编排 | FastAPI、Pydantic、LangGraph、MCP |
| 数据与检索 | MySQL、SQLAlchemy、Alembic、Redis、Milvus、BM25 |
| 模型服务 | OpenAI 兼容接口、独立 Embedding 与 Reranker 服务 |
| 部署与测试 | Docker Compose、pytest、Vitest |

## 快速启动

准备 Docker Compose 和 Windows PowerShell。首次运行时创建本地配置：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑 `.env`，设置数据库、MinIO 密码和至少32字符的 `AUTH_SECRET_KEY`，配置聊天模型、Embedding 与 Reranker 的地址及模型名称。密钥仅保存在本地配置中。

Compose 配置映射、模板默认值与当前本机运行快照见[配置说明](docs/CONFIGURATION.md)。

```powershell
.\scripts\start.ps1 -SkipSeed
```

脚本构建镜像、启动依赖、执行迁移后启动 API 与 Worker；`-SkipSeed` 跳过演示数据导入。本地模型按需使用 `-LocalEmbedding`、`-Reranker` 等选项，并配置对应服务地址。已有部署的升级与回滚请按[部署说明](docs/DEPLOYMENT.md)操作。

日常使用可双击 `start.bat` / `stop.bat`，仅启动或停止已有主栈、Embedding 与 Reranker 容器，保留部署镜像和配置，不构建、不迁移、不导入数据。首次安装仍使用上述 PowerShell 命令。

默认应用地址为 `http://localhost:8100`，健康检查与接口文档分别位于 `/health`、`/docs`。停止服务使用 `.\scripts\stop.ps1`，保留数据卷。

## 开发与验证

后端使用 Python 3.11+，安装依赖时同时使用约束文件：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -c requirements.lock
python -m compileall app scripts
python scripts/test_offline.py --profile active
```

隔离回归使用临时数据库与外部依赖替身，不调用真实供应商。完整配置和测试范围见[协作指南](AGENTS.md)。前端在 `frontend/` 下执行 `npm ci`、`npm run dev`；验证使用 `npm test` 和 `npm run build`。

## 项目结构

```text
app/          后端接口、Agent 编排、业务服务与数据访问
frontend/     前端应用
alembic/      数据库迁移
文档/         业务知识原文
docs/         架构、部署与验收说明
scripts/      启动、维护与评测工具
tests/        自动化回归测试
acceptance/   固定验收场景与材料
```

## 文档

- [架构与 Agent 链路](docs/AGENT_GRAPH.md) · [API 说明](docs/API.md)
- [部署说明](docs/DEPLOYMENT.md) · [安全与权限](docs/SECURITY.md)
- [文档分类](docs/README.md) · [脚本导航](scripts/README.md) · [验收材料](acceptance/README.md)

验收记录按版本和场景保存，测试结果的适用范围以对应报告为准。
