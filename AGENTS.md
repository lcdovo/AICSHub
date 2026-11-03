# 项目协作指南

## 沟通与工作范围

- 默认使用简体中文沟通，说明具体改动、验证结果及尚未验证的范围；代码标识符沿用现有命名。
- 修改前检查 `git status` 和相关源码，保留已有未提交改动，避免无关重构或批量格式化。
- 本指南描述项目约定；端口、模型和运行状态以当前配置、源码和实际检查为准。配置存在不代表服务已运行或模型已接入。

## 目录与职责

- `app/main.py`：FastAPI 入口、生命周期、中间件、健康检查与静态资源。
- `app/agent/graph.py`、`state.py`、`memory.py`：LangGraph 编排、轮次状态、会话与锁；业务节点在 `app/agent/nodes/`。
- `app/agent/`：意图识别、问题改写、校验、业务工具及 MCP 服务端/客户端；MCP 不可用时保留进程内工具回退。
- `app/routers/` 负责 HTTP/WebSocket 传输，`app/services/chat_service.py` 统一恢复状态、执行和落库；不要为两种传输另建业务链。
- `app/services/knowledge_base/`：知识库导入、检索、缓存失效；模型调用分别位于 `llm_service.py`、`embedding_service.py`、`reranker_service.py`。
- `app/models/`、`app/repositories/`、`app/schemas/` 分别负责持久化模型、数据访问与 Pydantic 契约；数据库迁移在 `alembic/`。
- `frontend/src/`：React + Vite + Tailwind 前端，页面在 `pages/`、组件在 `components/`；构建输出到 `static/`。
- `tests/`：pytest 回归测试；`scripts/`：启动、迁移调用、数据导入和评测脚本；`docs/`：架构、API、部署与安全说明。

## 环境与常用命令

Python 3.11+，Windows PowerShell。依赖以 `requirements.txt` 和 `requirements.lock` 为准；Docker 安装时同时使用二者。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -c requirements.lock
# 仅首次缺少 .env 时复制，避免覆盖已有配置
Copy-Item .env.example .env
python -m uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

本机运行 Python 时，需要把数据库、Redis 等连接地址改为宿主机地址。Compose 内的 `mysql`、`redis`、`milvus` 服务名不能直接用作本机地址；本地 SQLite 兼容路径和相关测试需要另装 `aiosqlite`。

- `docker compose up -d --build`：仅启动默认主栈 `app/mysql/redis/etcd/minio/milvus`。
- `.\scripts\start.ps1 -SkipSeed`：启动主栈并执行迁移，跳过演示数据导入；`-NoMigrate` 可跳过迁移。
- `start.bat`：主栈 + 本地 Embedding + Reranker，默认不启动本地 Qwen LLM。启动服务不等于应用会使用它，仍需核对对应 API 地址和模型配置。
- `-LocalEmbedding` 仅启用本地向量模型；`-Reranker` 启用重排模型；`-LocalModels` 同时启用本地 LLM 和 Embedding；`-Langfuse` 启用观测栈；`-All` 启用全部可选栈。
- `docker compose --profile local-llm up -d llm-local`：单独启动本地 Qwen LLM；模型权重缓存保留在 named volumes 中。
- `docker compose run --rm app alembic upgrade head`：执行数据库迁移。
- `.\scripts\stop.ps1` / `stop.bat`：停止对应服务并保留数据卷。`-Data`、`docker compose down -v` 会删数据；知识库导入的 `--clear` 会清理现有知识，不能当作普通验证命令执行。
- 前端首次运行 `cd frontend`、`npm ci`，随后使用 `npm run dev`。

默认宿主机端口：API `8100`、Vite `5174`、Redis `6380`、MySQL `3306`、Milvus `19530`；本地 LLM `8011`、Embedding `8012`、Reranker `7997`。API 和 Redis 容器内端口分别为 `8000`、`6379`。前端代理同步维护在 `frontend/vite.config.ts`；不要占用资镜使用的宿主机 `8000` 和 `6379`。

## 模型配置约定

- 当前主模型使用 UINO 的 `deepseek-flash`，接口为 `https://token.uino.com/v1`；意图识别、问题改写与回答生成共用主模型，保留现有规则优先策略。
- `LLM_THINKING_MODE=disabled` 关闭思考；`LLM_STRUCTURED_OUTPUT_METHOD=function_calling` 适配该接口的结构化输出。切换供应商时同时核对普通、结构化、流式调用和 `LLM_MODEL_VERSION`。
- `LLM_LOCAL_BASE`、`LLM_LOCAL_MODEL` 默认留空。本地 `cyankiwi/Qwen3-4B-Instruct-2507-AWQ-4bit` 仅在显式启动并配置后参与意图识别与问题改写；服务模型名为 `qwen3-4b-awq`。
- Embedding、Reranker 独立配置，不能随聊天模型一起替换；修改向量模型或维度时检查已有 Milvus collection 和索引兼容性。

## 必须保留的业务约束

- 每条消息通过 `AgentState.for_next_turn()` 创建独立轮次；只保留必要历史及未完成操作参数，禁止跨轮复用旧证据、生成草稿、校验或工具结果。
- 回答缓存必须隔离用户、上下文、当前证据及模型/校验配置；仅缓存通过最终校验且无业务副作用的回答。拒答、转人工及模型降级回复不入回答缓存。
- 最终回答在响应节点统一校验；生成模型自检不能替代独立证据检查。供应商草稿在服务端缓冲，验证通过后再发布给用户。
- REST 与 WebSocket 使用同一会话锁覆盖状态恢复、执行与持久化；保留锁所有权和租约检查。M2 聊天执行器还使用 SQL 会话租约与版本条件写入；Redis 获取前不可用时由 SQL 后备协调，已获取后失租则中止。该保证仅覆盖已验收的 Run/受保护业务路径，不能推广到任意旧入口。
- 退款等副作用保留权限、参数、幂等及本轮明确确认；历史确认不能授权新操作。Redis、Milvus、LLM 不可用时保留明确的降级路径。

- 知识写入使用稳定文档ID/版本CAS与持久索引Job；索引验证后才切换SQL发布指针。检索每次核对发布快照/epoch，禁止按标题删除或混用未知Embedding空间。取消发布不能被迟到索引/普通编辑自动恢复。

## 编码与验证

- Python 使用 4 空格缩进、类型标注及 `snake_case`，异步路由和服务避免阻塞操作；TypeScript 保持严格类型，变量用 `camelCase`、组件用 `PascalCase`。
- Python 改动后运行 `python -m compileall app scripts`，并按范围运行 `python -m pytest` 或对应测试文件；新增行为或修复缺陷时补有意义的回归测试。
- 状态、缓存、校验或并发修改优先检查 `tests/test_chat_turns.py`；模型接入检查 `tests/test_llm_provider_config.py`；配置、退款、检索有各自测试文件。
- 修改 API 时使用隔离数据库和依赖替身验证受影响接口，必要时检查 `/health`、`/docs`；模型实测使用最小合成输入，不发送真实用户数据。
- 前端改动后在 `frontend/` 运行 `npm test` 和 `npm run build`；Vitest 用例使用 `*.test.ts` / `*.test.tsx`。未找到测试文件须如实报告，不能视为测试通过。
- 区分配置检查、自动化回归、真实模型调用和完整部署验收；测试失败时说明具体用例和原因，不为使测试变绿而修改无关业务逻辑。

## 安全、提交与文档

- `.env`、API Key、Token、数据库密码和完整用户数据不得写入版本化文件、日志或回复；读取配置只选取所需非敏感字段，不整段输出 `.env`。
- `.env.example` 仅保存无密钥模板；生产要求强 `AUTH_SECRET_KEY`、明确 CORS origins、关闭演示账号并使用非演示凭据。不要新增数据库、日志或构建产物到提交中。
- 提交保持单一主题，沿用 `fix:`、`chore:`、`perf:`、`refactor(rag):` 等前缀，摘要可用中文。PR 说明行为/配置变化、验证命令，以及迁移、种子数据和外部服务影响。
- 架构变更同步相关文档，优先参考 `docs/AGENT_GRAPH.md`、`docs/API.md`、`docs/DEPLOYMENT.md`、`docs/SECURITY.md` 和 `docs/llm-governance.md`；文档与代码冲突时先核实实际实现。

## M4当前验收状态（2026-11-04）

- 本机业务知识配置已切向Ollama `qwen3-embedding:0.6b`（1024维、revision `ollama-ac6da0dfba84`）和Infinity `BAAI/bge-reranker-large`；是否推理成功仍以检索metadata为准。
- M4为候选，独立留出实体95.67%未达97%，保持REQUEST_UNDERSTANDING_MODE=rules/灰度0。未通过项见docs/M4-GATES-v1.json，不得把实现或替身通过写成完整验收。
- 当前业务SQL迁移0011、LLM_GATEWAY_ENABLED=true。新SQL网关保证仅覆盖启用网关且同SQL数据库的调用；候选代码已补齐直接图/聊天/RAG/兼容摘要的可信主体scope，无身份结构化请求关闭；历史system0保留，长期泄漏及部分真实模型故障仍有未验范围。
