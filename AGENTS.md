# 项目协作规范

## 项目概览

这是一个基于 FastAPI 的企业智能客服与工单自动处理系统。后端代码位于 `app/`，静态页面位于 `static/`，数据导入和维护脚本位于 `scripts/`。系统支持使用 SQLite 进行本地开发，也支持通过环境变量连接 MySQL、Redis 和 Milvus 等外部服务。

## 开发约定

- 使用 Python 3.11 或更高版本；依赖版本以 `requirements.txt` 为准。
- 新增或修改 Python 代码时遵循现有模块划分：Agent 逻辑放在 `app/agent/`，HTTP 路由放在 `app/routers/`，业务服务放在 `app/services/`，数据模型和校验模型分别放在 `app/models/` 与 `app/schemas/`。
- API 输入和输出优先使用 Pydantic 模型校验，数据库访问通过现有的异步 SQLAlchemy 工具完成。
- 保持异步调用链完整；不要在异步路由或服务中加入会阻塞事件循环的同步网络或数据库操作。
- 配置通过 `.env` 或 `.env.example` 管理。不要把 API 密钥、数据库密码、生产凭据或其他敏感信息提交到版本库。
- 用户可见文本和新增文档使用中文；代码中的变量名、函数名、接口路径和已有领域术语保持项目现有命名风格。
- 所有文本文件使用 UTF-8 编码并保留 LF 或项目原有换行格式，避免引入乱码或无关的全文件重排。

## Agent 与检索逻辑

- 修改 Agent 状态、节点或路由时，同时检查 `app/agent/state.py`、`app/agent/graph.py`、`app/agent/intent.py` 和 `app/agent/validation.py` 之间的数据契约。
- 工具调用应沿用现有的重试、超时、错误追踪和人工转接机制，不要绕过统一封装直接调用外部服务。
- 修改 RAG 检索、知识库或 Embedding 逻辑时，保留 Milvus 不可用时的内存降级路径，并验证 BM25、向量检索和重排结果的兼容性。
- 不要在日志中输出完整用户隐私、令牌或密钥；新增日志应包含必要的上下文和可关联的追踪信息。

## 验证与运行

提交前至少执行一次语法检查：

```powershell
python -m compileall app scripts
```

本地开发可先复制 `.env.example` 为 `.env`，然后使用：

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后检查 `http://localhost:8000/health` 和 `http://localhost:8000/docs`。如果改动涉及 Agent、数据库、检索或路由，应补充针对改动路径的手工验证，并在交付说明中注明已执行的检查及任何环境限制。

## 变更边界

- 只修改完成当前任务所需的文件；保留工作区中与当前任务无关的既有改动、删除和未跟踪文件。
- 不要提交生成的数据库、缓存、日志、`__pycache__` 或临时测试产物。
- 修改接口时同步更新相关页面、示例配置或 README 中受影响的说明；除非任务明确要求，不进行无关的重构或格式化。

## 提交与测试要求

- 每次改动完成后，都必须创建一个对应的 Git commit，以便后续追踪和回滚。
- 每次改动后，都必须编写或更新相关测试，并在交付给用户前，确保所有测试和验证全部通过。
