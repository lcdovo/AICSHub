# AI Customer

企业级智能客服与工单自动处理系统。基于 FastAPI + 官方 LangGraph 状态机，图形骨架由 LangGraph 提供，自研 13 节点业务编排（意图识别、RAG 检索、Function Calling 工具调用、人机协同、可观测性与 LLM 治理），覆盖完整闭环，重点展示单体应用内的模型编排与业务落地能力。

## 核心链路

```text
用户消息 → 全局追踪中间件 → Redis 会话状态恢复(hydrate_state)
→ Agent 状态机：
  start → intent_recognition(规则优先，低置信度 LLM 兜底)
       → route_decision(按意图分流)
       → (clarification | query_rewrite → rag_retrieval | slot_filling → tool_execution | confirmation | human_handoff | response_generation)
       → result_verification(事实/安全/完整性三层校验)
       → response_generation(LLM 生成 + 降级话术)
  → end
→ 落库(messages/agent_traces/evaluation_results) + 回写 Redis 会话
→ 流式输出(REST 分片 / WebSocket graph_updates)
```

7 类核心意图 + 1 个 `unknown` 兜底：`query_order`、`refund`、`complaint`、`technical`、`promotion`、`human`、`general`。退款意图内部进一步分流：政策咨询走 RAG，实际申请走工具并经 `confirmation` 节点二次确认。

## 当前实现重点

- **规则优先、LLM 兜底的意图识别**：`EnhancedIntentRecognizer` 关键词加权匹配 + 否定词修正，置信度 < 0.4 或需澄清时调用 `LLMService.classify_intent` 结构化兜底，关键意图仍由确定性规则主导；低置信度输入自动进入 `CLARIFICATION` 澄清节点；支持意图升级模式检测（如 `query_order → refund → complaint` 自动转人工），并记录 `intent_source` 便于审计。
- **混合 RAG 检索**：`KnowledgeBaseService` 实现 BM25 + 向量召回 + 分数融合 + 重排，支持查询扩展、同义词扩展、双向类目匹配；Milvus 不可用时降级到内存 + BM25，搜索结果中返回 `backend`、`degraded`、`degraded_reason`，不静默假装高可用。
- **组合式多轮查询改写**：`LLMQueryRewriter` 采用"规则优先、LLM 增强"策略——先是 `ConservativeQueryRewriter` 无开销地改写指代/省略类追问，规则未命中且模型可用时再由 `LLMService.run_structured` 做语义改写增强 RAG 召回，全程零延迟保底、可观测。
- **LLM-as-judge 防幻觉**：`validation_llm.py` 对 RAG/工具回答做事实一致性、引用对齐与"无据陈述"判定，无法锚定证据时选择"无据不答"并引导补充信息或转人工，而不是让模型为生成而编造；LLM 不可用自动退化为启发式校验。
- **跨会话长期记忆 / 用户画像**：`UserMemoryService` 聚合 SQLite 历史与工单数据形成用户画像，Embedding 可用时做向量回忆、否则退化为关键词召回与空画像；`get_user_history` 工具接入真实数据（去 Mock），并在响应生成节点将画像注入 LLM 提示词。
- **Function Calling 工具集**：8 个结构化工具（订单查询、退款申请、工单创建、知识检索、转人工、通知、工单状态更新、用户历史），统一封装参数校验、指数退避重试、超时控制、幂等缓存和简易熔断；通过 `langchain_tools` 转换为 LangChain `StructuredTool`。
- **退款二次确认**：使用 LangGraph `interrupt`，首次请求只写 `refund_preview` 与 `pending_confirmations`，用户明确确认后才调用 `apply_refund`，支持取消路径和会话/订单/类型组合的幂等键。
- **三层降级策略**：LLM 不可用 → 规则默认话术；Milvus 不可用 → 内存检索；工具失败 → 人工转接。
- **多轮对话记忆**：`SessionManager` 基于 Redis 持久化 `AgentState` 快照（TTL 24h），支持分布式锁、Token 触发的上下文压缩、槽位快照保留，Redis 不可用时降级为进程内存。
- **人机协同**：工单状态机（pending → processing → resolved/closed，支持 escalated）、转人工请求记录、客服负载分配、通知 outbox。
- **可观测性**：`X-Trace-Id` 全链路贯穿，节点 Span 写入 `agent_traces`；`/metrics` 输出 Prometheus 文本；评测结果写入 `evaluation_results`，低分样本自动进入低分池供后台处理。
- **LLM 治理基线**：`ArtifactRegistry` 管理 Prompt/Model/Schema/Route 版本与灰度；`BudgetManager` 控制单次与用户日预算；`SensitiveDataRedactor` 在模型边界脱敏；`PromptInjectionScanner` 拦截越权请求。
- **统一编排入口**：`ChatService` 让 REST 与 WebSocket 只在传输层不同，状态恢复、Agent 执行、结果落库、评测与 Trace 共用同一套逻辑。

## 项目结构

```text
app/
├── agent/              # Agent 状态机与编排
│   ├── graph.py        # AgentGraph：LangGraph 编排，run / run_stream
│   ├── state.py        # AgentState、AgentNode、IntentType
│   ├── intent.py       # 规则 + LLM 兜底意图识别
│   ├── tools.py        # 8 个业务工具与重试/熔断基类
│   ├── langchain_tools.py  # StructuredTool 适配
│   ├── query_rewrite.py    # 保守的上下文问题改写
│   ├── validation.py   # 事实/安全/完整性三层校验
│   └── memory.py       # Redis 会话状态与上下文压缩
├── routers/            # HTTP 路由（users/sessions/tickets/chat/analytics/feedback/knowledge）
├── services/           # 业务服务（chat_service/knowledge_base/llm_service/llm_governance/evaluation/collaboration/embedding_service）
├── models/models.py    # SQLAlchemy 模型
├── schemas/schemas.py  # Pydantic 输入输出模型
├── repositories/       # 幂等查询与状态变更仓储
├── utils/              # database、tracking、milvus_client
├── security.py         # JWT、RBAC、限流
├── config/config.py    # Pydantic Settings
└── main.py             # FastAPI 入口、中间件、健康检查
frontend/               # React + Vite + Tailwind 前端（构建产物部署到 static/）
static/                 # 构建后的前端静态资源
scripts/                # seed_ecommerce_orders.py、import_rag_documents.py、dev.ps1
docs/                   # API、AGENT_GRAPH、DEPLOYMENT、RAG_RELIABILITY、OBSERVABILITY、SECURITY、llm-governance、memory_architecture_optimization
alembic/                # 数据库迁移
docker-compose.yml      # 完整依赖栈部署
Dockerfile              # 应用镜像
requirements.txt        # 依赖
.env.example            # 配置模板
```

## 本地开发

依赖 Python 3.11+，依赖版本以 `requirements.txt` 为准。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# 按需修改 .env：DATABASE_URL_OVERRIDE 指向本地 SQLite，或配置 MySQL/Redis/Milvus

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后检查：

- 应用首页：<http://localhost:8000/>
- 健康检查：<http://localhost:8000/health>
- OpenAPI 文档：<http://localhost:8000/docs>

提交前执行语法检查：

```powershell
python -m compileall app scripts
```

## Docker 部署

项目只提供一套 Docker 部署：完整依赖栈，包括 MySQL、Redis、Milvus、etcd、MinIO 和应用服务。Compose 配置位于 [docker-compose.yml](docker-compose.yml)，不使用 profiles。

### 1. 准备环境变量

```powershell
Copy-Item .env.example .env
notepad .env
```

必须提供非空的 `MYSQL_ROOT_PASSWORD`、`MYSQL_APP_PASSWORD`、`MINIO_ROOT_PASSWORD` 和至少 32 字符的 `AUTH_SECRET_KEY`。生产环境还应填写 `LLM_API_BASE`、`LLM_API_KEY`、`EMBEDDING_API_BASE`、`EMBEDDING_API_KEY` 与模型名称。`.env` 不得提交到版本库。

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

### 3. 灌入测试数据

```powershell
docker compose exec app python scripts/seed_ecommerce_orders.py
docker compose exec app python scripts/import_rag_documents.py --clear
```

### 4. 停止与数据

```powershell
docker compose down
```

MySQL、Redis、Milvus、etcd 和 MinIO 数据保存在 Docker named volumes 中。`docker compose down -v` 会删除这些数据卷，请仅在确认需要清空数据时使用。

## 配置与安全

- `ENVIRONMENT` 默认 `integration`，`DEBUG=false`，`USE_MILVUS=true`，`REDIS_ENABLED=true`；`production` 会强制校验 `AUTH_SECRET_KEY` 长度、关闭演示账号并禁止通配 CORS。
- 生产环境必须使用随机的 `AUTH_SECRET_KEY`（至少 32 字符），保持 `AUTH_DEMO_ACCOUNTS_ENABLED=false`，配置明确的 `CORS_ALLOW_ORIGINS`。
- 登录与聊天分别限制每 IP/用户每分钟 10 次和 60 次；消息长度、上传大小与批量数量由环境变量控制。
- 外部服务不可用时，应用通过 `/health` 暴露降级原因，并按适配器降级；不替代对 MySQL、Redis、Milvus 和 LLM 的可用性监控。
- 完整变量说明参见 [.env.example](.env.example) 注释。

## 相关文档

- [API 交付说明](docs/API.md)
- [Agent 主链路](docs/AGENT_GRAPH.md)
- [Docker 部署](docs/DEPLOYMENT.md)
- [RAG 可靠性](docs/RAG_RELIABILITY.md)
- [评测与可观测性](docs/OBSERVABILITY.md)
- [认证与权限](docs/SECURITY.md)
- [LLM 治理基线](docs/llm-governance.md)
- [多轮对话记忆架构优化建议](docs/memory_architecture_optimization.md)
