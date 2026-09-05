# AI Customer

企业智能客服与工单自动处理系统：用户认证与资源隔离 → 意图路由 → RAG/工具调用 → 结果校验与人工兜底 → 数据持久化 → 指标评测与故障降级。

## 问题与方案

演示型客服常见的问题是状态只在内存、权限可被客户端伪造、外部依赖不可用时没有解释。项目用异步 SQLAlchemy + Alembic 持久化业务状态，用 JWT/RBAC 隔离资源，并将 Milvus、Redis、LLM 都封装为可观测的降级适配器。

## 快速开始（local profile）

要求 Python 3.11+、Docker（可选）。本地 profile 不需要 MySQL、Redis、Milvus 或真实 LLM Key：

```powershell
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
./scripts/dev.ps1
```

浏览器打开 <http://localhost:8000/docs>。也可以使用 Docker：

```powershell
docker compose --profile local up --build
```

local 使用 SQLite、内存/BM25 检索和 Mock LLM，明确用于演示与回归，不代表生产高可用。完整外部依赖使用 integration profile：复制 `.env.integration.example` 为 `.env.integration`，填入密码和模型配置后执行 `docker compose --env-file .env.integration --profile integration up --build`。

## 验证与 CI

```powershell
./scripts/ci.ps1                 # compileall + 单元/API 测试
./scripts/ci.ps1 -BuildImage     # 额外构建非 root 生产镜像
python scripts/rag_benchmark.py  # Recall@K/MRR/Precision@K/P50/P95
# 仅审计生产依赖（CI 也会执行）
python -m pip_audit -r requirements.txt --progress-spinner off
```

GitHub Actions 位于 `.github/workflows/ci.yml`，在干净 Python 3.11 环境执行语法检查、ruff、mypy、pytest 和 Docker 构建。集成测试需要外部服务，不在默认 CI 强制运行。

## 交付文档

- [部署与配置](docs/DEPLOYMENT.md)：profiles、健康检查、迁移和生产安全清单
- [API、错误码与权限](docs/API.md)
- [5 分钟演示与故障脚本](docs/DEMO.md)
- [RAG 可靠性与基线](docs/RAG_RELIABILITY.md)
- [评测与可观测性](docs/OBSERVABILITY.md)

## 当前可量化指标

运行 `python scripts/rag_benchmark.py` 会输出固定 QA 集的 Recall@K、MRR、Precision@K、空召回率以及检索延迟 P50/P95；`/metrics`（管理员 Token）提供 HTTP、Agent、工具、RAG、评测和人工转接指标。线上模型费用需按实际供应商账单补充，Mock/local 结果不得冒充生产 SLA。

## 权衡与演示

local profile 牺牲外部服务的高可用，换取无 Key、可重复的面试演示；integration profile 才启用 MySQL/Redis/Milvus 和真实 LLM。按 [5 分钟演示脚本](docs/DEMO.md) 可完整走通登录、知识库、RAG、订单、退款确认和转人工，并演示超时与依赖故障降级。

## 架构边界

LLM 负责理解、候选决策和自然语言生成；代码负责认证/RBAC、订单金额与退款资格、数据库事务/状态机/幂等、工具重试超时熔断、敏感信息和最终高风险动作校验。Prompt/模型版本、Token 与降级信息写入 Trace，便于问题复现与回滚。
