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

## 文件组织与维护（2026-11-09）

- 文档分类入口为 `docs/README.md`，脚本分类入口为 `scripts/README.md`，固定场景入口为 `acceptance/README.md`；新增、移动或更名文件时同步维护对应索引。
- 根目录保留启动入口、依赖、Compose、Docker、迁移配置和项目指南。业务实现放 `app/`，测试放 `tests/`，工具放 `scripts/`；不要把临时排查脚本或日志散放根目录。
- `文档/` 保存业务知识原文，`docs/` 保存架构、操作和验收说明，两者用途不同。导入器及来源身份引用现有知识路径，整理时不得随意移动或改名。
- `acceptance/` 的固定输入、版本与标签以及 `reports/` 的派发账本、原始回执、closeout和保护hash需保持可追溯；旧记录不能删除或覆盖为新成绩。目录索引只负责导航，不代表当前部署或验收结论。
- 本地临时归档统一放 `.local/`，已从Git与Docker构建上下文排除。2026-11-09将根目录 `_grep_trace_tmp.py` 和 `pull_infinity.log` 原样归档至 `.local/archive/2026-11-09/`，原路径和SHA-256记录在同目录 `manifest.json`；不得把含私有信息的归档加入提交。
- `.pytest_cache/`、`.ruff_cache/`、`__pycache__/` 属于工具缓存；`data/`、`static/`、模型缓存和容器数据卷不能按普通缓存处理。文件整理不自动授权数据删除、服务重启、部署或付费模型调用。
- Python回归默认通过 `python scripts/test_offline.py` 运行；启用配置链路使用 `--profile active`。下文历史 `python -m pytest` 示例须通过隔离入口执行，直接运行受 `tests/conftest.py` 保护；网络阻断尝试不能算通过证据。

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

- `docker compose up -d --build`：启动默认主栈 `app/worker/mysql/redis/etcd/minio/milvus`；已有数据库先迁移到当前head，再启动新API与Worker。
- `.\scripts\start.ps1 -SkipSeed`：启动主栈并执行迁移，跳过演示数据导入；`-NoMigrate` 可跳过迁移。
- `start.bat` / `stop.bat`：通过 `scripts/runtime.ps1` 启停已有主栈、Embedding和Reranker容器，保留当前镜像与配置；不构建、不迁移、不导入数据，不启动本地Qwen LLM。缺少API或Worker容器时启动失败，首次安装或部署使用 `scripts/start.ps1 -SkipSeed`。启停完成不等于服务健康，需核对状态。
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

## M4历史验收状态（2026-11-04，保留原阶段记录）

- 本机业务知识配置已切向Ollama `qwen3-embedding:0.6b`（1024维、revision `ollama-ac6da0dfba84`）和Infinity `BAAI/bge-reranker-large`；是否推理成功仍以检索metadata为准。
- M4为候选，独立留出实体95.67%未达97%，保持REQUEST_UNDERSTANDING_MODE=rules/灰度0。未通过项见docs/M4-GATES-v1.json，不得把实现或替身通过写成完整验收。
- 当前业务SQL迁移0011、LLM_GATEWAY_ENABLED=true。新SQL网关保证仅覆盖启用网关且同SQL数据库的调用；候选代码已补齐直接图/聊天/RAG/兼容摘要的可信主体scope，无身份结构化请求关闭；历史system0保留，长期泄漏及部分真实模型故障仍有未验范围。

- M4原授权真实供应商评测已用660/660请求；禁止通过换报告目录/删账本或自动重跑扩大额度。新增批量付费调用需新授权；继续本地/离线验收不受此费用额度影响。

## 当前本机部署（2026-11-07）

- 16节点图及有限任务循环已进入常规业务服务，REQUEST_UNDERSTANDING_MODE=model、100%，策略版本understanding.task-loop.v1。退款任务预览开启，提交仍为独立本轮确认的模拟申请，不含支付。
- 业务迁移0013_agent_reliability已执行，任务/步骤/能力执行账本及退款preconditions列已存在。API与常驻Worker共用同一镜像、SQL库和Redis模型准入namespace；Worker启动校验schema并以近期周期和进程启动标识做健康检查。
- 24份发布知识仍采用structured-source-v2、context-document-v2，共1067片段；新旧版本、旧collection及私有备份保留。原17条摘要积压已消费，历史失败索引Job保留。
- 本地Qwen3 Embedding仍为0.6b/1024维/原revision，GPU推理、批量1；BAAI/bge-reranker-large为GPU float32。Judge继续v1，未采纳ONNX/融合/跨请求批处理实验；LOCAL_MODEL_COORDINATOR=redis。跨容器合成互斥及真实检索通过，不等于生产容量或GPU驱动级取消验收。
- 本机LLM_MAX_COST_USD=0.2是单Run保守预留上限，仍受日预算/次数/时限约束，不是实际账单。Langfuse未运行故关闭上报，本地Trace保留。
- mcp==2.2.0、fastmcp==4.0.3已固定并在容器验证；使用公开Client内存传输，API启动预载依赖，正常MCP调用及不可用时直调回退均保留。不能外推stdio或长期稳定性。
- 旧任务阶段117/120调用保持；新授权v4为120请求，原93行保持，本次部署新增18次，总计111/120，剩余9次不得自动当新批次。其余13份派发账本hash保持。最终5轮REST及原生WS通过，合成账号已禁用、无退款申请。
- 容器回归覆盖2273项；四分片初始2272通过/1个未启用batch模式时序失败，该项保持原代码与断言串行复验通过。不得写成单次零失败全量或生产并发通过。当前部署详见docs/RELIABILITY-ROLLOUT-20261107.md、reports/reliability-rollout/closeout.json；上一轮记录仍保留。

## 多意图逐项完成候选（2026-11-07）

- 工作区新增AGENT_ITEM_COMPLETION_ENABLED默认false；2至4项需求逐项检索/绑定本轮证据/独立Judge覆盖，所有answered后最终completed，limited/waiting_feedback/awaiting_confirmation不得冒称完成。存量TaskProgress JSON兼容，无新增SQL迁移；当前业务镜像未替换。
- 首次源码容器分片2306全部通过。真实首次6次发现Judge摘录含自身编号被误拒；修正并保存原捕获回执后，最终容器定向200项通过，不能把修正前全量称为最终源码全量。
- 用户新授权语义阶段80次，原6次加获准复验6次共12/80，余68；其中1次Judge10秒超时为unknown_dispatched、usage未知，已保守计入额度，不自动重放。修正版仅完成2/16组件任务，余14例及4个整链NOT_RUN，未完整通过语义准入，未启用候选。
- 新账本reports/multi-intent/live-budget/898af4b30533bac28a82565e61a90e3c8870820c07ea10d75a9d009e9ae008e6/dispatch.sqlite；原6行和14份旧账本保持。原111/120及剩余9次与本阶段分开，禁止删账本/换目录重置额度。继续真实复验须遵守原失败停止规则并取得明确续验指令。
- 详见docs/MULTI-INTENT-COMPLETION-v1.md及reports/multi-intent/closeout.json。合成assistant标签与SDK替身不能代替独立人工金标或真实模型语义质量；旧九个业务服务仍健康。

### 多意图受控续验v3最新状态（2026-11-07，以上12/80为历史）

- 用户授权“继续受控复验，通过后再启用”。原80总额度和12条记录保留，原unknown保守计账且不可重放；revision-v3/固定48组件+4整链各最多5次，新增最多68。
- app业务源码与v2保持一致，仅修改评测续跑保护及其测试。最终源码容器一次全量2311通过、宿主相关216通过；完整SDK演练66合成请求通过，真实付费0，不等于语义验收。
- 真实新增3次后在MI-01正确候选Judge 10秒超时处按组件失败停止；理解覆盖3/3，坏候选合法拒绝，其余15组件及4整链NOT_RUN。累计15/80，剩65，13 http_complete及2 unknown_dispatched，两条未知usage保留，不推定未计费。原12行与14份旧账本保持，旧111/120及9次余量未用。
- 候选镜像已构建并通过pip check/断网配置及Worker导入，仅准备未部署。现行业务镜像831db200…和九个healthy服务保持，多意图开关仍关闭，真实准入未通过；没有放宽时限或自动重试。评测容器和临时凭据已清理。最新证据reports/multi-intent/controlled-v3-closeout.json，继续真实派发须遵守失败停止并取得明确续验指令。

### 多意图验收v8最新状态（2026-11-07，前述计数为历史）

- 逐项模式先强制输出1—4诉求，程序推导task_kind，provider不再决定是否进入逐项；同订单多字段仍分项。动态逐项Judge schema限制合法阶段，但answerability与退款完成分离；单项兼容、原权限/确认/当前证据规则保留。程序跳过无剩余执行动作的finish模型调用，最终独立审查不跳过。
- 最终v8源码164cbabe…宿主/容器相关各391通过，SDK62次合成请求通过。最终不是一次全量；v4全量2313中初始2312通过/1已知未启用batch时序失败、原样串行复验通过，发生在最终schema修复之前。
- 用户另授权24、12、6次均单独账本；最新原80/80、新24/24、新12为0/12、新6为0/6，共104/122，剩18；原2条unknown usage保留，不退额度。原v8前79行和14份旧账本保持，旧111/120及9余量未用。
- 用户明确批准等价契约分段续验：保留v7前6项、MI-07理解和坏候选捕获重解析，v8新增25次后在MI-15留出停止。累计15/16组件覆盖、14正确候选接纳、0错误误放；MI-15把明确“质量问题”条件误判遗漏。合法Judge输出不等于语义通过，不得忽略条件标记、改留出标签或无限续跑。MI-16及4个真实整链、生成文本审查均NOT_RUN。
- 语义准入结论SEMANTIC_ADMISSION_FAILED_HOLDOUT_FALSE_REJECTION_NOT_ENABLED，功能未启用、候选镜像未部署，原0013 API/Worker镜像831db200…和九个healthy服务保持。临时容器/凭据清理，详见reports/multi-intent/controlled-v8-closeout.json与docs/MULTI-INTENT-COMPLETION-v1.md；失败后新真实派发需要明确续验方案/指令，剩18不足完整重跑64，禁止重置预算。

### Judge v2逐项接入最新状态（2026-11-07）

- 已完成v2逐句claim与逐项完成同调用接入，按段落约束各项来源，当前预览/用户报告独立标注，发布重核输入和item-context指纹；归一化item_checks不能篡改原始回执。配置允许v2逐项，v1保留，默认和业务.env不改，未部署。
- 最终宿主/断网容器相关各340通过；完整SDK演练6组正反候选+1整链，16合成HTTP、付费0。此为相关回归与替身，不是最终全量或语义通过。
- 原剩余12/6账户内做最多18次诊断，仅MI-15首组2次：正确候选function arguments含裸full值，非法JSON严格拒绝unknown，无自动修复/重试；坏候选合法拒绝。未超时/截断，后5组和整链NOT_RUN，不得宣称v2更可靠或正式准入通过。捕获及回归保留。
- 最新总量106/122，剩16，分别80/80、24/24、2/12、0/6，历史2条usage未知保留；原80/24及所有保护账本hash保持，旧111/120阶段未用。业务0013 API/Worker原镜像和九healthy服务保持，临时评测容器/凭据已清理。失败后续验需明确指令和固定方案。
- 详见docs/JUDGE-V2-ITEM-INTEGRATION-v1.md和reports/multi-intent/item-v2/closeout.json；前述v8状态及数字按历史保留。

### Judge v2独立复验最新状态（2026-11-07）

- 用户要求“验证”，在既有剩16次内执行6组正反候选+1整链有界方案。app/提示不变，独立item-v2-verify/前缀和原2条hash保护。实际新增6次，6条JSON均合法，不能据此证明协议稳定；MI-15/MI-04两组通过、MI-07好候选通过，但坏候选摘录跳过来源JSON中间order_id，非连续原文，严格核验unknown并拦截。按失败停止，余3组与整链未运行，不得把unknown算合法语义拒绝。
- 最终捕获回归后宿主/断网容器相关各342通过；非最终全量。新增captured-item-v2-quote-gap-v1.json和回归；业务源码/提示保持。最新账本分别80/80、24/24、8/12、0/6，总112/122、剩10，历史2条usage未知不退额度；原记录和保护账本保持。旧111/120阶段不动。
- v2仍未部署/启用，原0013 API/Worker及九healthy服务保持，临时评测容器/凭据已清理。状态V2_VERIFICATION_STOPPED_EVIDENCE_QUOTE_BINDING_FAILURE_NOT_ADMITTED；见reports/multi-intent/item-v2-verification/closeout.json。继续真实调用遵守失败停止规则，需要明确续验指令和固定额度方案。

### 服务端证据片段ID最新状态（2026-11-07，以上为历史）

- 用户授权进行1/2/3：逐项v2供应商仅返回fragment_id，服务端绑定本轮上下文/来源身份/版本/全文，还原完整原文后继续严格v2和逐项来源核验。不截断/拼接证据、不自动修补JSON、不放宽权限/阶段；普通非逐项v2保留。
- 最终宿主相关366通过、SDK10次合成请求通过；非全量。本轮Docker引擎不可用，未运行容器回归，不能把历史九healthy推广成本轮状态；业务.env/部署未改，无临时容器或凭据文件创建。
- 独立前缀item-v2-fragments/固定3组正反候选6次+1整链最多4次，共10上限。实际6次：退款预览/条件不满足两组通过；排障MI-13正确候选item_checks引用了claim未审的来源，unknown并拒绝，坏候选合法拒绝。失败停止，真实整链NOT_RUN；不得删除该一致性校验使其变绿。
- 累计118/122、剩4，账户80/80、24/24、12/12、2/6，原8条v2派发和保护账本保持，历史两条usage未知保留。旧111/120阶段未用。状态SERVER_FRAGMENT_REFERENCE_IMPLEMENTED_DIAGNOSTIC_STOPPED_ITEM_CLAIM_REFERENCE_MISMATCH，未启用，见reports/multi-intent/item-v2-fragments/closeout.json和docs/JUDGE-V2-ITEM-INTEGRATION-v1.md。后续真实派发须明确续验指令及固定方案，不能重置账户。

### 单一引用来源与额度收口最新状态（2026-11-07）

- 逐项v2 wire契约server-fragments.v2，item_checks不再输出evidence_ids；服务端从各claim所属事项及已验证片段推导来源。独立状态/条件/语义及跨项、版本、权限、退款确认底线保持，不通过删除校验掩盖冲突。
- 原最后4次用于MI-01真实整链冒烟（理解/执行决策/生成/Judge各1次），草稿前含非事实礼貌开场，桥接要求全部segment在事项段落内导致失败拦截。red回归复现后离线修复：非事实开场可在段外，事实claim仍必须在对应事项内，非事实不提供来源或完成事项。
- 最终宿主/断网容器相关各373通过，修复后SDK4次合成整链通过；不是全量/真实修复通过。真实失败与最终来源分开，captured-derived-e2e-v1.json和失败记录保持。功能未部署启用，业务.env未改。
- 总122/122、余0：80/80、24/24、12/12、6/6，历史两条未知usage保留；此前14条v2记录及保护账本hash保持，旧111/120阶段未用。新付费调用必须新授权，不可借旧9次/换报告目录重置。
- Docker初期不可用后恢复，补跑临时容器已自动删除；检查时app/mysql/redis/milvus/etcd/minio exited、worker restarting，embedding-local/reranker healthy。本轮未启停业务主栈，不得继续引用旧九healthy作现状。最新状态DERIVED_ITEM_SOURCES_IMPLEMENTED_REAL_SMOKE_FAILED_PREAMBLE_OFFLINE_FIXED_NOT_ADMITTED，见reports/multi-intent/item-v2-derived/closeout.json及docs/JUDGE-V2-ITEM-INTEGRATION-v1.md。

### 固定Judge v2新增80阶段最新状态（前述122/122保持）

- 用户明确授权新增80次：16旧回归+4新合成场景的理解/正反候选60次，4完整图最多16次，验收通过后部署冒烟最多4次；输入预留1966080 UTF-8字节，输出163840 tokens。每任务失败/unknown即停，无自动重试/JSON修补，最终文本独立复核后才能启用。新例非人工金标。
- 最终相关宿主/容器各377通过，SDK20例+4图74次合成HTTP通过。业务Judge保持v2统一片段引用/程序推导逐项来源。准备/替身/路径错误均派发前修正并保留，不能当真实质量证据。
- 真实仅MI-01三次：理解3/3，正确候选function arguments再次裸full非法JSON，unknown拦截；坏候选合法拒绝。无超时/截断，按失败停止。余19例/4图/文本复核/部署冒烟均未运行。不得靠反复原样重跑/修改标签/弱化解析声称通过。
- 新账本reports/multi-intent/live-budget/f3659b32128b67fca75b24c67567aab6d031caa7ab0fbb16195ad8f91d1728ef/dispatch.sqlite，3/80、剩77，3 http_complete、usage已知；原18份保护账本hash保持、原122/122及旧111/120不动。失败后真实续验须明确指令和可审查方案，不能自动重放或重置账户。
- 状态V2_FIXED_ACCEPTANCE_STOPPED_RECURRING_INVALID_PROVIDER_JSON_NOT_ENABLED，临时容器/凭据清理、未部署启用。本轮未启动主业务栈；app/mysql/redis/milvus/etcd/minio exited、worker restarting、embedding-local/reranker healthy。见docs/JUDGE-V2-FINAL-ACCEPTANCE-v1.md和reports/multi-intent/v2-final-acceptance/closeout.json。

### JSON协议与脱敏安全ID最新状态（2026-11-08，以上3/80为历史）

- 用户要求修复复验，并明确批准原80内剩62次分段方案。v2新增可选LLM_JUDGE_V2_OUTPUT_PROTOCOL=json_object（默认function_calling、业务.env未变），请求JSON对象约束及完整schema，仅v2单次调用，严格json.loads/类型/完成原因校验，无JSON修补、协议失败回退或自动重试；v1/理解/生成保留原协议，协议纳入策略绑定。
- 原函数参数裸full来自供应商回执，旧路径仅本地schema无显式JSON约束；新JSON阶段10个、后续2个Judge回包共12个语法合法，不能外推稳定性。首次续验15次在MI-05发现本方手机号脱敏regex误伤十六进制fragment hash，原红灯回归复现后改为无数字a-p可逆编码，保留完整256位及真实手机号脱敏，不削弱来源/版本/跨轮绑定。
- 获准分段前4项保留为历史（ID编码变化，不是最终源码相同输入完整运行）；后续3次MI-05理解完整、坏候选合法拒绝，正确候选把segment0同时归为claims/nonfact，严格unknown拦截。按失败停止，剩15组件（含4新例）/4图/文本复核/部署冒烟未跑，不能忽略互斥校验宣布通过。
- 最终宿主及断网容器相关各425通过，源码稳定/网络阻断0，非全量；SDK20组件+4图74次合成调用通过。新增captured-v2-overlap-v1.json保留真实失败并验证不静默删除重复分类。没有后续app修改把原失败追写为成功。
- 本阶段21/80、剩59，21 http_complete且usage已知；原3条/随后18条逐列绑定和18份旧保护账本保持，旧122/122、111/120余9及历史2未知usage不动。临时容器/凭据清理，业务未启停/部署；主app/mysql/redis/milvus/etcd/minio仍exited，worker restarting/starting，本地向量及重排healthy。
- 状态JSON_PROTOCOL_AND_REDACTION_SAFE_IDS_FIXED_REVALIDATION_STOPPED_FACT_NONFACT_OVERLAP，见docs/JUDGE-V2-JSON-PROTOCOL-FIX-v1.md及reports/multi-intent/safe-id-revalidation/closeout.json。失败后真实续验仍需明确指令/固定方案，剩59不是自动重跑许可。


### Judge v2生成文本复核（2026-11-08，之前条目为历史）

- 当前新阶段117/127，旧122/122及旧111/120不动。剩10含指定不自动使用余量2。布局四图自动3通过/1停止，但独立文本复核发现MI-06无依据配件/处理后果被general_advice误放，故不满足准入。MI-16 answered+partial被最终管线正确拦截。
- 已修订生成与Judge提示并更新缓存策略版本；新提示真实语义未复验，原20项正反判决不再作为新提示成绩。没有启用或部署；业务依赖仍停止，空转Worker已停止，不能沿用历史九服务健康。
- 下一固定方案为44次正反审查+四图最多20次+通过后部署4次，共68；现有可用8，新增60尚未授权。不得因本条计划自动派发、消耗保留2次或重置旧账本。详见docs/JUDGE-V2-ATOMIC-POLICY-RECHECK-v1.md及reports/multi-intent/atomic-policy-recheck/closeout.json。


### Judge v2分句复验准备（2026-11-08，之前计数为历史）

- 新增60已授权，固定68实际用了61；当前总178/187，余9中2指定保留，可用7。原22组真实组件全通过，MI-01/04/06整链通过；MI-16分号混合事实/资料不足被Judge列为空证据事实，仍严格拦截。
- 已修复legacy subrequests为空时多项政策误走单项RAG，保留旧单项条件咨询路径；最终逐项渲染只为中文分号追加换行，字词与分号全保留，Judge提示及证据门槛不改。22项输入/证据/schema与3图无分号输出已核对等价，最终按分段证据陈述。
- 最终宿主/断网容器各474相关测试通过，SDK80合成HTTP通过，付费0；新镜像构建、依赖/Worker/MCP导入通过，但未部署、未启用。最后MI16最多6+部署最多4共10，需额外3次授权；候选新3次账本0行且confirmed=false，不能自动派发。详见reports/multi-intent/clause-layout-revalidation/closeout.json。


### 当前本机Judge v2部署（2026-11-08，覆盖此前未启用/回滚的运行状态）

- 用户明确要求“修复并部署”。已修复实际政策多句重复schema导致请求超过24576字节，以及入口40句/内部20条事实的不一致。逐项来源、条件、独立Judge和发布绑定保留；schema按事项复用、每句独立审查。
- API/Worker已部署ai-customer:v2-payload-fix-r2-20261108，共用同一SQL/Redis准入namespace，190份源码核对一致、无临时挂载；九服务healthy、Worker健康检查及health/docs/openapi通过，SQL仍0013。逐项检查=true、Judge=v2、协议=json_object、MAX_TOKENS=2048、MAX_RETRIES=0。
- 最终宿主/断网容器相关各553通过，非全仓全量；共享schema修复镜像真实REST两项订单/SQL/Redis/Judge及WS greeting通过，合成账号禁用、退款申请0。最终边界修复镜像无新增真实调用，不能写成最终镜像一次全链付费验收。
- 本次新增2次供应商HTTP均完成且usage已知，当前284/286，余2指定保留不可自动使用；旧账本/unknown保留。原包装问答修复后仅做实际知识正文的请求大小和完整回执离线回放，尚无新的真实供应商包装语义成绩。
- MI16及相同范围家族的旧completed标签存在争议；本轮不改旧标签，不使用旧四图全completed作为最终门槛，不宣称全量语义重新准入。详见docs/JUDGE-V2-DEPLOYMENT-FIX-20261108.md与reports/multi-intent/deployment-payload-fix-r2/closeout.json。


### 多意图评测标准v2与指定知识语义复验（2026-11-08）

- 完成24例标准复核，新增scenarios-reviewed-v2.json及multi_intent_oracle.py，MI15/16、V2S01/02四例泛问运费但只有质量条件证据改为limited/partial可成功且waiting_input；旧标签/成绩不改。v2评分分离协议、陈述支持、事项覆盖和任务阶段；不以全部completed作为安全答复统一通过门槛。
- 用户明确授权使用保留2次，两次独立真实Judge对当前发布包装政策语义审查通过，已知合成订单completed，缺订单号waiting_input仍评测成功；包装损坏替代方案保留。输入理解/订单为合成，检索政策为真实发布快照，非两次API整链。
- 宿主/断网容器相关各588通过、SDK2合成HTTP通过；app源码/业务配置/部署镜像本轮未改，线上Judge v2逐项功能保持启用、九服务healthy。指定两项工作完成，但不得宣称24例新真实模型全跑、独立人工金标或生产容量验收。
- 当前阶段286/286余0，保留2已获明确授权并消耗；旧账本与unknown不变。禁止自动新增供应商评测。证据docs/MULTI-INTENT-ORACLE-V2-ACCEPTANCE-20261108.md和reports/multi-intent/oracle-v2-runtime/closeout.json。


### 重排batch20最小部署（2026-11-08，覆盖此前batch4运行状态）

- 基于原业务镜像ac4b75b0…，仅加入配置/重排批大小/检索缓存身份三文件补丁，API与Worker同镜像d8b78495…，batch20、redis单槽同namespace；不是当前工作区整体部署。
- 隔离断网相关123通过；真实知识检索部署前3组A/B/A共9次、部署后API/Worker各3次，片段/正文hash/epoch一致、无降级，20候选重排HTTP5→1。付费聊天0；非完整真实聊天/生产容量验收。九服务healthy、SQL仍0013。
- 有效运行环境只新增RERANKER_LOCAL_BATCH_SIZE=20，温度/时限/LLM并发/TOP_K及Embedding其他候选未启用。旧镜像及私有精确Compose覆盖保留；维护/回滚按docs/RERANK-BATCH20-ROLLOUT-20261108.md操作，勿用历史JSON覆盖或默认latest误部署工作区。


### 默认测试隔离与同版本回归（2026-11-08）

- 默认测试通过python scripts/test_offline.py运行；直接python -m pytest由tests/conftest.py在业务导入前阻止。启用链路使用--profile active，保留model/任务循环/逐项/Judge v2 JSON及SQL网关，用替身隔离外部依赖。任何网络阻断尝试即使被测试捕获也拒绝通过证据。
- 一条命令python scripts/verify_offline_release.py分别验证工作区与当前API/Worker不可变镜像；只用本地测试wheel，不自动联网下载、不重启业务。最终两侧各261通过、零失败/跳过/阻断，旧单项兼容与隔离保护另38通过；相关切片，不是全仓或真实语义验收。
- 源码差异14处保持，业务仍d8b78495…/batch20，未部署其余候选。最新证据reports/offline-release/20261108-134818-200521/closeout.json，说明docs/OFFLINE-RELEASE-VERIFICATION-20261108.md。付费派发0，旧账本不变。


### 新业务场景验收准备（2026-11-08）

- acceptance/business-v1新增14开发+6最终固定场景，共28轮，7个epoch25发布片段及完整hash；assistant编写标签，不是独立人工金标。真实20场景REST/WS及生成文本复核NOT_RUN。
- scripts/business_acceptance.py仅prepare/rehearse；SDK/账本/评分最终28合成HTTP通过、付费0。实际REST/WS+独立SQL适配器已补齐，无模型寒暄/重放预检通过，不冒称20场景语义通过。宿主相关47、部署镜像断网相关44通过，网络阻断0。
- 最终准备方案revision api-adapter-preflight-v2，prepared_id 1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042。提议新额度最多168供应商HTTP（28轮各最多6），输入预留4128768 UTF-8字节、输出344064 tokens；当前confirmed=false，不能自动派发或借用任何旧余量。33份旧dispatch账本hash保持。
- 原未授权SDK准备草案与旧合成演练已保留，非付费额度重置。当前业务仍d8b78495…/batch20、九healthy；源码/材料/镜像变更需显式修订固定方案，失败/unknown停止，不自动重试/JSON修补。说明docs/BUSINESS-ACCEPTANCE-PLAN-v1.md，证据reports/business-acceptance-v1/closeout.json。


### 新业务真实验收v1（2026-11-08，覆盖上一条未授权状态）

- 用户确认新增168次，实际9/168、余159，9条供应商usage均已知，原33份保护账本保持。BA-01/02/03最终答复独立assistant文本复核通过（证据直出，不是自由生成质量）；BA-04/t1单项WS生成请求被旧评测账本的stream禁止规则在真实派发前拦截，按首错停止。BA-04/t2及余16例/6最终例NOT_RUN，整体未准入。
- 已用捕获前2响应断网复现，3个请求body hash与真实逐个一致，确认evaluation_stream_not_authorized；不是模型语义失败样本。隔离SQL的1条generation_stream保守unknown预留原样保留，不能混同供应商9条已知usage或伪称第10次真实HTTP。
- 新native SSE usage计账候选10项SDK替身检查通过，未用于真实调用/未部署。业务仍d8b78495…/batch20，原业务环境未改。临时评测凭据清理，原始失败及Mock准备失败均保留。
- 固定续验提案：同一账户余159内最多新增148次，前三通过场景余11slot退休；精确hash复用BA-04已成功理解/决策2条，原9行保护、剩余操作不重放、首错/unknown再停。提案未确认，STOP保留；真实续验须明确指令，不因余量自动重跑。见docs/BUSINESS-ACCEPTANCE-LIVE-v1.md及reports/business-acceptance-v1/live/1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042/closeout.json。


### 评测链修复与业务续验v1（2026-11-08，覆盖上述9/168状态）

- 用户明确“盘查修复…然后继续”，按同一168账户最多新增148执行，本次新增7，总16/168、余152；16条供应商usage全部已知，原9行逐列及33旧账本hash保持。148续验余额141也不得自动使用；新STOP保留。
- 新增三个评测模块business_http_boundary/business_acceptance_execute/continue_business_acceptance及两个回归文件，原业务app与冻结案例未改。修复native SSE/usage结算、gzip、取消/截断、停止闸门、语义失败误作等待反馈、Windows路径、原子构建包及凭据/容器清理。最终宿主相关93、目标镜像断网90通过；单项/多项×REST/WS矩阵通过，非全仓/生产容量。宿主Python3.13 SDK异步生成器结束警告保留，目标镜像3.11本轮无该警告。
- BA-04两轮真实SSE与最终文本复核通过；原成功理解/决策2条精确body hash回放，不重复计费，是分段等价复验。累计BA01–04场景通过；BA05/t1供应商合法JSON但apply+无conditions+单prepare_refund附read_tasks触发read_plan_requires_query，已安全停止，无退款预览或申请。BA05/t2及BA06–20/最终6例NOT_RUN，整体未准入。
- 确认逐项提示的前置读表述与单项无条件申请空read_tasks约束冲突；仅提示澄清候选在reports下，6项离线契约通过，坏回执仍拒绝、正确样本仍程序先查单。未改校验/自动修补/部署/真实复验；新提示若采用，原4例不能算新提示质量成绩。
- 业务仍d8b78495…/batch20、九healthy，原SQL保守unknown预留1条原样保留且不混同真实16条已知usage。最新docs/BUSINESS-ACCEPTANCE-CONTINUATION-v1.md及reports/business-acceptance-v1/live/1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042/continuation-v1/closeout.json。下一真实派发需明确新固定方案和续验指令，不借余量自动重跑。


### 退款读计划v2与材料预检最新状态（2026-11-08）

- 用户授权开始并先修错误。已应用单项无条件申请read_tasks=[]的提示澄清，程序仍先查单；原语义、权限、资格和本轮确认校验保留。业务.env/部署未改。
- 新固定步骤为BA-05预览后取消及BA-10预览后确认，最多24次，同原168账户。真实新增3次，理解正确，查单/检索后模型建议人工而未生成预览；按首错停止。当前19/168、余149，本轮余21保持停止，非自动重试授权。旧16行、33外部账本及所有STOP保留，其他历史账户不动。
- 离线回放复现安全转人工；发现原正向退款案例只提供包装/运费片段、缺资格/质量/时限/审核材料。新增内容hash绑定证据预检及acceptance/business-v2独立材料候选（SQL发布epoch25完整片段），不修改旧标签/成绩，不强迫生成预览。材料缺口不是已证明的唯一因果，范围覆盖不证明具体退款资格或模型通过。
- 最终宿主/断网容器相关各155通过，非全量；正式派发前各60与SDK22合成HTTP通过。正式执行源码和停止后离线预检源码分开记录，无停止后新付费调用。当前API/Worker仍d8b78495…/batch20，九服务healthy，无评测临时凭据/容器。
- 后续先复核候选材料及状态预期，再固定续验；不得删除STOP/自动重放/换目录重置额度。详见docs/BUSINESS-REFUND-SCOPE-REVALIDATION-v2.md与reports/business-acceptance-v1/live/1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042/refund-scope-v2/closeout.json。


### 退款材料/状态复核v3最新状态（2026-11-08）

- 用户“进行下一步”授权完成复核及同账户有界续验。确认v2原4字原因未满足现有6字要求，程序未开放prepare_refund；不能把原转人工归因为模型放弃可执行预览。原政策材料缺口为独立问题，旧失败/标签保留。
- app/agent/task_loop.py候选取消前三片段/600字静默截断，完整片段知识投影超过8192 UTF-8字节在模型前拒绝；不放宽权限/确认。新独立reviewed-v3案例使用详细逐字原因及程序原支持的“取消/确认”，不等价旧输入，也不证明长句授权支持。
- 最终宿主/断网容器相关各241通过，非全量；SDK四格22+退款6共28合成HTTP通过。原镜像只读覆盖request_understanding/task_loop两候选文件后，真实REST预览取消、WS预览本轮确认两例通过独立文本及隔离SQL复核；新增6次理解/决策HTTP，无真实生成/Judge调用，最终正文为服务端模板。
- 累计25/168，usage全部已知，余143；本轮21上限余15不自动使用。原19行、33份外部账本与全部历史STOP保持。取消案例申请0；确认仅1笔is_simulated/pending_review，未支付，不接触业务SQL副作用。
- 未部署/未改业务.env/迁移，API/Worker仍d8b78495…/batch20，九healthy；临时容器/凭据/锁清理。两例不代表20例语义准入或生产容量；下一步短原因定向补问及长句确认/取消契约。详见docs/BUSINESS-REFUND-REVIEWED-v3.md及reports/business-acceptance-v1/live/1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042/refund-reviewed-v3/closeout.json。


### 退款原因追问与明确长句确认最新状态（2026-11-08）

- 用户“进行下一步”已完成本轮工作区实现：单项退款原因未满足原6字要求时，确定补问具体原因，跳过规划模型选择转人工；不提前预览/提交，补充后仍查单/检索/核验。多项范围未扩展。
- 有限长句确认/取消采用严格整句白名单，不关键词匹配；确认类型必须匹配当前预览。聊天节点、轮次状态、SQL最终提交与提交后恢复共用识别函数。实际本轮Message、可信主体、参数/版本、资格、租约/幂等规则保留。否定/条件/引用/历史/变订单金额/附加指令不授予确认。
- 最终compileall通过，宿主与断网容器相关各297通过，源码一致稳定、网络阻断0，非全仓全量；REST/WS三轮补原因/长句确认取消、SQL反例、类型不符、崩溃恢复均覆盖。初版及源码漂移被拒报告保持，不当最终证据。
- 容器使用当前工作区源码副本和不可变镜像依赖，不是线上原源码验收。付费0，原25/168及余143保持，33外部账本/历史STOP不变；没有真实新契约模型语义成绩，没有部署或业务.env/迁移修改。九业务服务healthy。
- 详情docs/REFUND-LANGUAGE-CONTRACT-v1.md及reports/refund-language-20261108/closeout.json。下一步先绑定候选版本与新契约固定验收，再决定部署；不能自动重放旧失败或重置预算。

### 退款七例受控验收最新状态（2026-11-08）

- 用户授权修复并“授权所有需要的额度”；解释为具体固定方案上限96新增，同原168账户，不是无限额度。保留各版本原STOP与回执，绝不重置原25条。
- 已修复否定确认导致回复声称“无待确认操作”与SQL仍pending不一致；改为保留待确认状态、明确本轮未提交。添加单项无条件申请内部读计划仅服务端推导；供应商wire不再有read_tasks/task_kind，旧字段仍严格拒绝，条件、权限和合法显式查单保留。换订单且明确仅查询时执行新订单只读计划，不提交旧预览。
- v3在LC-05明确查单未完成处停止，累计67/168，失败STOP与回包保留。修复明确只读路由后冻结v4，宿主/容器相关各421通过、七例SDK25合成HTTP通过。
- v4以独立LANG4前缀在同一账户重验七例：LC01/02/03/04/05/06/07依次通过隔离真实REST/WS API、完整回执和逐例独立助理文本/SQL审查。检查覆盖短原因追问→详细原因→长句取消/确认；否定、条件撤销；明确新订单查询；类型不符；历史确认不复用。确认场景恰一笔pending_review模拟申请，未支付；LC05只读新单，原预览仍pending。七例是助手撰写合成场景，不是独立人工金标。
- 当前原账本91/168，原基线25，本系列共新增66：LANG1 12、LANG2 12、LANG3 18、LANG4 24；usage未知0，尚有原授权容量30，但不自动延展。33份历史账本hash、25行基线及全部旧STOP保留；从失败修订重开的付费回执保留、审查绑定最终版本。
- 最新候选通过宿主/容器421相关回归，非全仓；网络阻断0，SDK25 mock。线上仍d8b78495…同镜像九服务healthy；未部署、业务.env/SQL未改，临时凭据/容器/锁清理。故障后的真实新增继续以首错停止，离线修复需新源码/预检/固定绑定，剩余30不得隐式消费。
- 详见docs/REFUND-LANGUAGE-LIVE-ACCEPTANCE-v1.md和reports/refund-language-live-v4/closeout.json。其余17业务验收场景、独立人工金标和生产容量仍未验收，不部署候选。


### 退款七案例固定方案最终状态（2026-11-08）

- v4真实受控七案例均完成并逐条独立正文/SQL回执审查：短原因补问/长句取消、WS本轮确认、否定确认维持pending、条件确认撤销旧预览、新订单显式只读、退款类型不符、历史确认不复用。LC05只读新订单且不提交旧订单；无支付。
- 原168账户91/168，基线25，新增66（LANG1-4 12/12/18/24），usage未知0、剩30。原账本25行、33外部账本及所有版本失败STOP保留；失败调用计账，没有退额度/重置。追加测试或真实调用不得借剩余30自动扩大。
- v4最终源码宿主/断网容器相关各421通过，SDK七案例25次synthetic HTTP、真实派发24次。不是全仓、独立人工金标、所有业务准入或生产容量验收。候选未部署；原API/Worker镜像仍在，九服务healthy，业务.env/SQL未改，支付未调用。
- 最终机器收口reports/refund-language-live-v4/closeout.json，历次失败与修复说明docs/REFUND-LANGUAGE-LIVE-ACCEPTANCE-v1.md。其余17业务案例、部署及回滚未验收，不得声称整体项目通过。


### 降级与测量口径优化候选（2026-11-08）

- 用户“进行优化”已实现：政策金额门槛/订单金额局部作用域，退款时限表格/实际到账区分；实际金额错误及支付声明仍拦截，独立Judge保留。
- 结构化调用增加白名单失败码，本地预算/scope拒绝不熔断供应商或转备用重试；理解失败按预算/并发/超时/协议给准确信息。失败码不保存原异常文本。
- 单一明确设备操作检索增加有限来源词匹配，缓存版本更新；无匹配本轮来源时追问品牌型号/现象，不宣称全库无知识，不创建人工单。通用保修/多设备路径不变。该保守过滤未测真实召回质量，不等于语义检索准入。
- 新统计按真实结果分组P95，不凭正文“超时”等词判断；业务成功须显式语义审查，不以旧启发式当金标。历史160条重新分类，活动6条不再误算降级。旧未持久计账的160调用脚本入口禁用，真实性能批次需固定独立计划与账本。
- 最终宿主/断网容器同候选相关各244通过、源码稳定、网络阻断0，compileall通过，非全量；付费0、旧91/168及33外部账本不变。未部署，原九服务healthy，无当前版新P95/真实语义成绩。旧退款阶段余30不自动变成性能预算。
- docs/DEGRADATION-OPTIMIZATION-20261108.md及reports/degradation-optimization-20261108/closeout.json记录修复/边界，禁止宣称P95已降低或线上已生效。


### 降级优化最小发布与性能停止最新状态（2026-11-08，覆盖此前运行镜像状态）

- 用户“逐个进行”后已完成8组真实Judge组件正反审查4接纳/4拒绝，付费8，原业务验收账本99/168、usage未知0；仅合成组件语义，不是API全链或人工金标。证据reports/optimization-gate-v1/closeout.json。
- 仅8优化文件从旧d8镜像形成补丁，未部署工作区退款长句、新事项wire、任务路由和新温度配置等候选。实际切换API/Worker、health/docs/openapi200，回滚旧d8验证后恢复；第一性能批次发现政策独立列表金额误拦，再回滚、补真实列表红回归、构建并发布v2。
- 当前API/Worker同镜像sha256:20f856b0fd1ccf332d93f45eb07e697389d7029e19e0bb6a5916667656382250，tag ai-customer:degradation-opt-v2-20261108；9服务healthy。业务.env/迁移0013/模型空间/数据卷不变。精确部署/回滚覆盖reports/optimization-release-v2/private/*-exact.yml含凭据，勿输出/提交；普通Compose/latest不保证该候选。镜像相关187通过、2个工作区未部署cache hook测试明确不适用排除，非全仓。
- 真实性能第一批8HTTP/13模型调用在多意图金额校验处停止，原失败保留；v2修复后单例1HTTP/3调用通过精确SQL Run+Checkpoint复核，测试脚本状态码判断错误不把它算计时样本，不重发原请求。第二固定160HTTP批次51HTTP后在refund/7 Judge unknown停止，尚未达到每类20；各意图仅6-7，50系统accepted/1unknown，不能报稳定正常业务P95或全业务成功。
- 第二批83模型SQL调用均成功且usage已知；性能相关13+3+83共99，另组件账户8次单独记录，原99/168不与性能journal重置/混算。性能每样本新合成用户、SQL网关预留每Run最多6/输出2048/重试0，本地journal派发前记录HTTP，所有失败保留。
- 技术问题6例是证据不足补问不是解决，人工登记不是接入；活动含其他时期政策的时效正确性未独立验收，系统Judge accepted不代替人工金标。新旧混预算P95不直接比较。原160测量入口已禁用。
- unknown原始供应商回执缺失，只知4条模型调用均成功，最终reason被旧assess压成judge_unknown；不能断言协议/绑定/预算唯一原因。已离线修复安全原因白名单保留，unknown仍拒绝发布、不缓存/不重试。该额外diagnostic补丁未部署，宿主/断网容器相关各48通过，未再付费。
- 60合成账号全禁用、7人工测试请求全取消，退款申请0、投诉工单0。收口reports/optimization-sequential-closeout.json与docs/OPTIMIZATION-RELEASE-20261108.md，性能reports/intent-p95-optimization-v2/closeout.json。步骤1/2完成，步骤3未完成，禁止写成全链全部正常或P95已稳定下降。


### Judge诊断部署最新状态（2026-11-08）

- 当前API/Worker同镜像2a08a526014ff5ad5785255114d09aaadf9042ae0b4745dbdc1110e71c9fbbd6，tag judge-diagnostics-v3-20261108，九healthy。在上轮20f优化上仅改llm_service/validation_llm/answer_review诊断三文件，配置/迁移不变。
- 增加输入hash、回执hash/字节/结束原因、有界脱敏摘录/解析判决；修复发布核验丢诊断及async ContextVar子任务元数据丢失，显式绑定返回对象传递。unknown仍拒绝发布、不缓存、不修JSON、不自动重试。
- 3次有界真实HTTP、9模型调用usage已知，另1次启动连接失败0模型调用；前2次诊断缺失记录保留，最终单例完整诊断及发布验证通过。4合成用户禁用、退款申请0，未续跑性能。原refund7故障未复现，根因仍未证实。
- 相关测试分阶段48/66/26及专项6通过，最终镜像compileall，非最终镜像全仓回归。详情docs/JUDGE-DIAGNOSTICS-20261108.md和reports/judge-diagnostics-release-v3/closeout.json。单例成功不代表可靠性或P95通过，性能51样本STOP保持。


### 退款十条有界复验后续（2026-11-08）

- 当前部署2a08诊断v3镜像相关67通过；最多10条真实退款复验首条unknown即停止，余9未跑。实际4模型调用succeeded、usage已知，用户禁用、退款/投诉申请0，未恢复性能批次。
- 失败码judge_failed_or_invalid/输入hash保留，但失败分支receipt仍null；旧成功路径验证不能推广到失败路径。已离线复现并修复子任务返回None时元数据丢失，同子任务显式返回value+metadata；unknown仍拒绝发布，不重试。
- 新修复宿主/断网容器相关各50通过，非全量，暂未部署。业务仍2a08镜像九healthy，原失败根因仍未知，不宣称JSON/预算/绑定唯一原因。证据reports/judge-refund-recheck-v1/closeout.json及docs/JUDGE-DIAGNOSTICS-20261108.md，余9不自动重放。


### Judge双分类根因与互斥wire最新状态（2026-11-08）

- 用户要求继续查因，部署失败子任务诊断返回补丁后，固定批次2条/6调用中捕获第二条：合法JSON、finish=stop，segment7/8同时claims与nonfact。严格校验正确拒绝；此次是接口双通道结构缺陷，问题清楚且3个相关发布片段足以回答。旧无回执unknown不能全部追认同因。
- 新非逐项v2 exclusive segments wire每句单一分类，服务端绑定原句再走原JudgeVerdictV2证据/版本/摘录/覆盖审查；不删旧冲突字段、不放宽校验、不重试。逐项selector不变。默认开关false，运行显式LLM_JUDGE_SEGMENT_WIRE=true、prompt judge-exclusive.v1。
- 当前API/Worker镜像543f4f132770f2a3fe50780b6ef1b74746308d77c574d1f38745c10f7ea4c80f，tag judge-exclusive-v1-20261108；9healthy，无迁移。初始镜像56相关、宿主专项7通过；不是最终全仓。
- 修复后固定相同退款问题10HTTP/30模型调用全部系统审查accepted、usage已知，全部合成账号禁用、退款/投诉0；不是所有业务/稳定P95或独立人工金标。保留失败v1/v2与原性能STOP，不混算历史。报告reports/judge-refund-exclusive-v1/closeout.json及docs/JUDGE-DIAGNOSTICS-20261108.md。用户通用政策问题不需订单号，后续优化可收敛不必要扩展回复。


### Judge单句互斥与诊断收口（2026-11-08）

- 捕获退款包装问答中segment7/8同时进入claims/nonfact_segments，原严格互斥检查正确拒绝；用户问题合理、3份发布知识支持，不能归因资料缺失。此前无回执的unknown不能倒推同因。
- 非逐项v2启用每句一个互斥segments.sN结构，绑定原句后仍执行原覆盖/来源/版本/连续摘录校验；旧冲突不修补。逐项selector不变。真实固定问题10/10 REST通过、30模型调用usage已知，账号禁用，无退款/工单/转人工；非全业务或稳定P95。
- 当前API/Worker同镜像84729bcf…（judge-contract-diagnostics-v1-20261108），LLM_JUDGE_SEGMENT_WIRE=true，保留judge-exclusive.v1；仅增加绑定错误码允许列表传递。断网容器65相关通过、compileall通过，非全仓；最终诊断补丁无新增真实付费调用。九服务healthy、health/docs/openapi通过。详见docs/JUDGE-DIAGNOSTICS-20261108.md及reports/judge-contract-diagnostics-v1/。


### 本地旧版本清理（2026-11-09）

- 已删除32个停止的本项目实验容器及44组未使用候选镜像；未使用强制删除或删除数据卷，其他项目资源保留。
- 当前API/Worker镜像为 `af0109c9115b`（`ai-customer:latest`），保留最近回滚镜像 `84729bcf5fe7`（`judge-contract-diagnostics-v1-20261108`）；清理后九个业务服务仍healthy。该状态仅为本次检查快照。
- 历史发布文档中的更早镜像已可能不在本机，不能直接照旧回滚命令执行。历史说明、固定验收输入、原始回执与账本仍保留，不作为清理目标；执行回滚前重新核对目标镜像及对应精确配置。
- 已清理22个工具缓存目录；`.pytest_cache` 因访问权限保留。详细删除清单及实验容器末尾日志位于Git忽略目录 `.local/cleanup/20261109/`，不可提交其中的本地日志。
