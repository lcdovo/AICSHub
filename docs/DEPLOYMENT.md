# Docker 部署

> **当前快照（2026-11-11）：**本机API与Worker运行同一镜像，容器健康，数据库迁移为0013。Judge v2/JSON、model理解、任务循环和多意图逐项在当前实例配置中开启。配置及快照证据见[配置来源与当前运行快照](CONFIGURATION.md)；历史文档中的“未部署/未启用”只描述其记录日期对应的阶段。

### 时间戳约定

运行时/API/序列化时间统一通过 `app.utils.utc_time.utc_now()` 生成带时区 UTC 值。现有数据库列仍是 `DateTime(timezone=False)`，写入与比较使用 `utc_now_db()`，明确保存 naive UTC；不要在未完成数据库迁移和历史数据校准前把两种类型直接混比。`scripts/test_offline.py` 将运行工作目录与 pytest `basetemp` 分开放在 `.local/offline-test-runs/`。2026-11-11 全量回归未通过，报告 **19 项失败**：1 项网关测试中的动态导入调用被本轮迁移误改（已还原，定向复验通过），其余 18 项集中在退款事项端到端断言；没有在还原后重跑全量，因此不能把18项写成当前全量失败数或通过结论。选择器与 pytest 选项分流已修正，并分别验证了 `-k` 单独使用及与测试路径并用；本次不以完整套件通过作为验收结论。

本地模型`LOCAL_MODEL_COORDINATOR=batch`是默认关闭的单进程实验候选，不能与其他API/Job进程共用同一模型资源并声称全局限流；WORKERS大于1时拒绝。仅隔离候选测试启用，不修改业务`.env`，不在在途执行时热切配置。Embedding微批与重排独立通道的原5秒截止保持，完整压力延迟仍FAIL；运行与回退记录见[M5-CROSS-REQUEST-BATCH-v1.md](M5-CROSS-REQUEST-BATCH-v1.md)。

## M1固定收尾迁移与验收环境

新增0007_m1_refund_actions：订单金额改Numeric(12,2)，新增签收/币种/已退金额、退款动作与事务闸门、申请审核字段。历史签收保持NULL；缺失时不能批准申请。迁移先拒绝非有限、负数或不能精确表示两位小数的旧金额，不自动猜测或舍入。独立MySQL已演练0006旧列到0007及99.10金额/用户记录保留；真实业务库未迁移。

升级前备份并验证恢复，先迁移后完整切换worker；旧worker不具备action约束，不能新旧混跑。0007拒绝有损自动降级，回滚必须使用经过验证的备份恢复方案。本轮未授权执行业务迁移或部署。

测试使用aicshub-m1-acceptance-*专有MySQL/Redis容器、tmpfs和新建合成库，入口scripts/test_m1_real.py验证容器标签后运行；运行状态/生成密码保存在忽略的reports/m1-closeout/runtime。浏览器入口acceptance/m1/server.py绑定动态localhost端口并只服务reports/m1-closeout/frontend-build，保留同一合成库用于进程重启验证。不要把验收凭据用于真实环境，也不要用down -v或清空业务数据作为验证步骤。

## M1-11 人工分配升级补充

新增迁移`0005_handoff_capacity`只建立`handoff_capacity_gates`事务闸门表，不修改历史人工指派或种入演示座席。切换分配写流量前需迁移并确保所有worker使用新分配路径及一致的名册/容量配置；旧版本直接写ASSIGNED可绕过闸门，不应混跑。此轮仅在临时SQLite演练新增迁移的upgrade/downgrade/upgrade，没有在当前业务库迁移或部署。

真实MySQL行锁、双worker、部署与备份恢复尚未验收。降级应先关闭分配写入；旧实现可能超额分配，不作为安全回滚目标，也不能在新worker运行时删除闸门表。此补充不表示下文历史部署说明已完整核验，实际Compose与启动参数以当前配置为准。

当前默认Compose主栈为app/mysql/redis/etcd/minio/milvus；可选模型与Langfuse使用profiles，具体以仓库docker-compose.yml和AGENTS.md为准。

## 首次部署

前端生产资源在 Docker 的 Node 构建阶段从 frontend/ 源码生成，复制到容器内 /app/static/；不再依赖 Git 中预存的静态构建文件。本地可运行 cd frontend 后执行 npm ci、npm test、npm run build；Vite 输出仍为根目录 static/，资源路径 /static/。部署前应先在隔离环境检查页面，不要用前端构建替代 API / 模型验收。

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up -d --build
docker compose ps
```

`.env` 必须提供非空的 `MYSQL_ROOT_PASSWORD`、`MYSQL_APP_PASSWORD`、`MINIO_ROOT_PASSWORD` 和至少 32 字符的 `AUTH_SECRET_KEY`。真实 LLM/Embedding 服务还需要填写对应的 API 地址、密钥和模型名称。

首次启动依赖健康后，在应用容器中执行 Alembic 迁移：

```powershell
docker compose run --rm app alembic upgrade head
```

迁移完成后访问 `/health`；应用和所有依赖正常时，数据库、Redis 和 Milvus 应显示为 healthy。Compose 已配置健康检查、依赖启动顺序和持久化卷。

## 日常操作

```powershell
docker compose up -d                 # 启动或恢复全部服务
docker compose logs -f app           # 查看应用日志
docker compose restart app           # 仅重启应用
docker compose pull                  # 拉取新基础镜像
docker compose up -d --build app     # 发布新的应用代码
docker compose down                  # 停止服务，保留数据卷
```

数据卷包括 `mysql_data`、`redis_data`、`etcd_data`、`minio_data` 和 `milvus_data`。`docker compose down -v` 会删除它们，执行前必须确认已经完成备份。

## 安全上线清单

1. 使用随机且至少 32 字符的 `AUTH_SECRET_KEY`，不要使用示例值。
2. 保持 `DEBUG=false` 和 `AUTH_DEMO_ACCOUNTS_ENABLED=false`。
3. 设置明确的 `CORS_ALLOW_ORIGINS`，禁止生产环境使用 `*` 配合 credentials。
4. 通过密钥管理系统注入 MySQL、MinIO、LLM 和 Embedding 凭据，不要写入镜像或日志。
5. 首次上线先执行 `alembic upgrade head`，确认迁移成功后再接入流量。
6. 为 MySQL、Milvus、MinIO 数据卷制定备份和恢复方案。

## 健康检查与故障

- `GET /health` 返回应用、数据库、Redis、Milvus 和 LLM 状态及降级原因。
- `GET /docs` 提供 OpenAPI 文档。
- 如果 MySQL、Redis 或 Milvus 尚未 healthy，应用容器会等待依赖；使用 `docker compose ps` 和 `docker compose logs <service>` 定位问题。
- LLM 或 Embedding 不可用时，应用会记录可解释的降级信息；生产环境仍应配置外部服务的超时、重试和监控。


## M2迁移和独立worker

`0008_m2_runs_jobs`增加会话version/context_data/摘要游标/租约/tombstone、可空messages.run_id、chat_runs/run_events/background_jobs/simulated_deliveries及通知sending/unknown/回执字段。真实隔离MySQL演练旧列升级与历史记录保留；不为历史消息伪造Run。未执行真实业务库迁移。升级应先备份验证、停止旧写入worker，迁移后完整切换，不能混跑旧绕过fence的worker；downgrade拒绝有损自动回退。

独立消费入口 `python scripts/run_worker.py --once --kind summary`；持续运行省略--once。通知默认未接渠道，显式运行得到failed/channel_unavailable；隔离测试使用 `--kind notification --simulate`，只落模拟回执，不外发。每进程顺序消费，每轮默认10任务，种类可分进程；每任务默认3次尝试、退避上限300秒，unknown先对账。worker也扫描到期Run并只按持久回执恢复。web不会自动启动worker。

M2真实验收入口 `python scripts/test_m2_real.py` 校验专属容器标签/前缀，再为每个用例建立合成库；runtime位于忽略的reports/m2-closeout/runtime。不得指向业务资源或清理业务卷。索引处理器未注册时明确failed，真实知识索引算法归M3；模型/真实通知/生产部署不是本次通过范围。证据见 `M2-EVIDENCE-v1.md`。


## M3知识迁移与索引切换

0009_m3_knowledge_versions新增稳定ID、不可变版本、发布指针、知识epoch、chunk原文位置/hash/上下文，不删除历史原文或旧collection。迁移只在本轮合成MySQL/SQLite演练；真实业务库尚未迁移。旧发布正文保留SQL关键词降级，旧向量指纹未知，不与新空间混用；显式重建新版本并验证后才发布。升级前备份原文/关系库并验证恢复，先迁移后完整切换worker，不能新旧写路径混跑。拒绝有损自动降级。

保存知识只排队Job；独立 `python scripts/run_worker.py --kind knowledge_index` 消费，运行/费用必须另行安排。worker严格批量向量化，指纹命名的新collection使用dense索引；应用层SQL快照BM25与RRF融合，不声称原生FULLTEXT。索引错误保留原发布版；失败Job可由管理员审计重试。清理历史chunk/collection不在本次执行范围。

隔离入口 `python scripts/setup_m3_test.py` 只创建aicshub.test.owner=m3-closeout专属MySQL/Redis/etcd/minio/Milvus；`python scripts/test_m3_real.py`核对标签后每例建合成库。runtime密码在忽略目录，不能改指向业务资源。真实模型入口有独立2请求预算账本，已授权额度用尽，禁止自行再次运行。供应商FreeTierOnly免费额度耗尽导致真实接入未验收，详见证据文档。

## 本机本地知识模型与M4候选（2026-11-04）

业务容器Embedding指向`http://embedding-local:11434/v1`、`qwen3-embedding:0.6b`、1024维、revision `ollama-ac6da0dfba84`；Reranker指向`http://reranker:7997`、`BAAI/bge-reranker-large`，聊天仍为UINO。不随聊天模型替换知识空间。24文档新指纹索引验证后原子切换；旧版本/collection/卷保留。详细证据和备份见LOCAL-MODEL-SWITCH-v1.md。

Embedding批量2、导入HTTP时限90秒（检索调用仍有独立5秒外层边界）。Infinity CPU线程2、batch4，禁用torch compile/全尺寸启动warmup；健康后必须实际推理核验，不能只看进程。旧模型权重卷复用，不下载替代模型。主栈迁移到0011；0010新增模型预算/调用/槽，0011补版本/价格快照，升级前备份。

业务显式LLM_GATEWAY_ENABLED=true，REQUEST_UNDERSTANDING_MODE=rules，percentage=0；M4未过门槛时保持此模式。新环境默认网关false为兼容开关，不能宣称已有SQL预算保证；启用前先迁移。旧实例/未升级进程不在新SQL网关并发保证范围。账号实际扣费尚未对账，LLM_PRICE_VERIFIED=false。

Judge契约默认值仍是v1；本机当前实例显式运行v2与`json_object`，见[当前配置快照](CONFIGURATION.md)。本段有关“仅离线验证、未启用”的文字是早期部署记录，保留其阶段性证据边界，不表示当前开关状态或完整语义准入结论。缓存key按契约及审查prompt/schema隔离，背景设计见M5-JUDGE-CONTRACT-v2.md。


## RAG结构索引升级（2026-11-07）

本次代码引入structured-source-v2和context-document-v2，Embedding指纹包含文档检索文本策略。旧发布版本保留词法读取，旧指纹不参与新空间dense检索；要取得新分块和完整混合检索能力，需安排读端与worker升级及新版本索引重建/验证后原子切换。没有新增SQL迁移，不删除旧版本/collection。不得把代码更新等同于业务知识已重建；本轮未运行业务reindex或真实模型验收。流程、默认参数和验证边界见RAG-STRUCTURE-v2.md。

## 任务循环候选的部署顺序

本阶段新增0012_agent_tasks（任务/步骤表及refund_actions.preconditions）。新镜像即使关闭任务循环也会读取扩展后的RefundAction模型，因此必须先迁移数据库再切镜像。迁移兼容旧NULL条件，不删除历史动作；回退通过关闭AGENT_TASK_LOOP_ENABLED，不执行有损downgrade。

开关默认false。启用要求REQUEST_UNDERSTANDING_MODE=model、LLM_GATEWAY_ENABLED=true、AGENT_TASK_MAX_OUTPUT_TOKENS不超过LLM_MAX_TOKENS。默认3次决策/4次能力/20秒调度阶段/256决策输出tokens，仍共享整Run网关预算。需先按[验收文档](AGENT-TASK-LOOP-ACCEPTANCE-v1.md)完成真实模型和运行验收；原请求理解通过不能替代新任务准入。
> 2026-11-07 本机已切换最新16节点Agent及0012迁移，任务循环和模拟退款预览开启；24份发布文档采用structured-source-v2/context-document-v2新索引。Embedding和同模型float32重排使用GPU，Embedding批量1，单Run模型费用预留上限0.2美元，Langfuse未运行故关闭上报。备份、恢复演练、清理与验证见 [统一升级记录](LATEST-ROLLOUT-20261107.md)。这些是本机配置，不自动作为其他主机或生产容量建议。

后续Agent可靠性开发新增0013_agent_reliability（agent_capability_calls独立执行账本）。本段“切换前”及“当前业务未应用”的判断是该阶段的历史部署准备记录；2026-11-11本机已运行0013，当前API/Worker镜像与状态见[配置快照](CONFIGURATION.md)。旧表、原回执和知识索引不清理；实现离线验证与限制见[Agent可靠性优化](AGENT-RELIABILITY-v1.md)。


### 2026-11-08 最小降级优化发布v2

截至2026-11-08的部署记录：本机API/Worker运行ai-customer:degradation-opt-v2-20261108（20f856b0…），仅8文件覆盖旧d8镜像。其余工作区候选未部署。使用reports/optimization-release-v2/private/candidate-exact.yml和rollback-exact.yml做精确切换，文件含凭据不得输出/提交；不要用latest或普通Compose推定候选。业务配置/SQL0013/数据卷不变，实际回滚演练已执行；当时九服务healthy。性能先导批次在Judge unknown处停止，各意图仅6-7样本，不代表完整准入。此处运行镜像描述为历史快照；当前配置见[配置来源与当前运行快照](CONFIGURATION.md)。详见OPTIMIZATION-RELEASE-20261108.md。
