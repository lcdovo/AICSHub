# 2026-11-07 统一升级与冗余代码清理

> 后续0013、MCP依赖修复及API/Worker部署已完成，当前状态见[可靠性部署收口](RELIABILITY-ROLLOUT-20261107.md)。本文保留0012及原93次调用阶段的历史证据。

范围：用户截图中的Agent编排、请求理解、回答流水线、排障/退款通知、任务API/持久化、0012迁移、RAG检索和知识索引。保留主聊天模型deepseek-flash、Judge v1及业务安全约束；不采纳未通过性能门槛的ONNX/融合/跨请求批处理候选。

## 代码清理

- 删除生产已不使用的`knowledge_base/retrieval.py`旧混合检索/启发式执行链和`index_admin.py`空兼容Mixin；移除IngestionMixin空类及旧检索状态/常量。
- 保留原文切分兼容入口DocumentChunker，仍共用结构化分块；保留仍有实际调用方的Agent节点兼容入口及规则模式。权限、确认、租约和降级机制不因清理删减。
- 将生产RRF融合和SQL向量余弦计算收拢为`ranking.py`，版本化检索和相应用例共用该实现。保留完整候选池、原始分数和重排失败保留RRF排名的行为。移除仅验证已删除旁路的用例，现有生产检索集成测试继续覆盖成功/失败/降级。
- 删除已引用不存在方法的旧`instrument_rag.py`插桩脚本；阶段耗时从检索metadata及现有评测读取。
- 源码证据工具将Git明确删除的文件记录为deleted_input_paths，稳定删除不再被误当读取失败；运行中删除仍改变起止文件集合并拒绝证据。临时Git仓库回归验证稳定删除、运行中删除和提交删除的指纹行为。

## 验证及备份

- 清理定向75项通过：`reports/offline/20261107-112007-812189`。清理中两次漏改旧局部变量造成的失败原报告保留，修正后再验证，未对失败测试放宽断言。
- 最终Python代码冻结全量：`reports/offline/20261107-112117-620559`，2230通过、12813条既有警告、551.81秒；pytest/证据退出码0，来源起止稳定完整，blocked_network_operations=0。
- 本机GPU Compose配置最终落地后的配置/provider切片：`reports/offline/20261107-113300-625914`，10通过，来源稳定；`docker compose config --quiet`通过。后一次仅有本地Embedding设备声明变化，Python业务代码与全量通过版本一致。
- 数据库在线一致性备份已在独立临时MySQL成功恢复，24份发布文档保留；停应用后另外保存最终静默备份。旧镜像保留为`ai-customer:before-latest-rollout-20261107`，私有.env备份及SQL压缩备份位于忽略目录`reports/latest-rollout/private/`，不得加入版本控制或公开输出。

## 本机模型及配置

Qwen3 Embedding仍为同一`qwen3-embedding:0.6b`、digest ac6da0dfba84、1024维。CPU两条较长合成文本约9秒，超过现有本地截止；GPU预热后两条批次约0.4秒，而20条仍约17秒，因此本机`EMBEDDING_BATCH_SIZE=1`，没有放宽截止或截短文本。Compose为该可选Embedding服务声明GPU；重排仍为BAAI/bge-reranker-large，真实推理已验证。

目标配置为`AGENT_TASK_LOOP_ENABLED=true`、`AGENT_TASK_REFUND_PREVIEW_ENABLED=true`、`REQUEST_UNDERSTANDING_VERSION=understanding.task-loop.v1`、`CHUNK_SIZE=500`。退款预览只是模拟申请准备，提交仍要求本轮明确确认、SQL权限/资格/原始条件/幂等校验，不包含真实支付。

## 索引升级

只枚举升级前当前已发布的24份文档，按原发布版本的正文/标题/来源生成新的不可变版本；不拿最新失败草稿替换业务原文。逐份通过真实Embedding、Milvus写入完整性检查后才切SQL发布指针，正文hash必须与原发布版本相同。

新策略为structured-source-v2和context-document-v2，使用新Embedding指纹collection。旧SQL版本和旧Milvus collection保留，不按标题删除、不清数据卷。首次脚本租约token超过MySQL字段长度时事务拒绝，改成标准UUID后继续同一已登记Job，未额外创建重复索引版本。

索引进度与最终记录：`reports/latest-rollout/runtime/index-upgrade.json`。部署及真实接口冒烟结果将在完成后追加。

## 维护期间问题与收口

- 首个20条Embedding批次的GPU冷启动超时，单独预热后成功；20条长批次仍约17秒，两条常见批次约0.4秒。重建第17份文档出现本地模型busy/截止，Job保持可重试、原发布指针未变；调整为1条批量后复用相同版本/Job完成余下文档。最终24份全部发布、1067片段，正文hash与旧发布版本逐份一致。
- 第一次HTTP冒烟在第三次模型请求前被单轮费用预算拦截，前两次派发仍计账；原0.05美元上限不足以容纳真实长证据多步骤链。调整本机`LLM_MAX_COST_USD=0.2`，保留日预算、每轮最多6次模型尝试、Run/步骤时限及已授权阶段120次总上限。价格字段为现有配置估算，未核对供应商账单。
- Langfuse没有运行，应用原配置却开启上报，已关闭`LANGFUSE_ENABLED`消除无效远端等待；本地Trace继续保留。
- 第二次冒烟出现订单/政策已取得后再次选择同一检索，旧重复保护安全停止。补充allowed_actions仅提供尚未完成的只读能力；知识已满足时不再提供重复检索。售后还缺refund_policy时仍可查政策，排障既有重复检索收尾保持。新相关134项通过：`reports/offline/20261107-114030-385792`。最终全量回归运行中，旧失败回执保留。

## 真实部署补齐

- 模型仍曾忽略提示中的allowed_actions，因此在实际供应商JSON Schema层按当前允许动作生成判别联合类型，完成能力不再出现在schema。保留原服务端动作范围/次数/确认校验，不能仅依赖schema授权。相关105项通过：`reports/offline/20261107-114832-589676`。
- 实际售后查询曾丢失“包装”等具体问题，改为保留retrieval_query并添加政策侧重点；决策观察对知识检索直接提供本轮精确证据摘要，不再发送截断的整段原始JSON。相关143项通过：`reports/offline/20261107-115207-812217`。
- 原附加参考来源正文包含“未发货订单”的另一政策分支，导致已签收订单的正确正文在最终出口被识别为矛盾。来源改为标题与精确chunk/version引用，不再追加未审正文；引用身份、正文事实、最终安全检查均继续执行，回答策略指纹升级。相关179项通过：`reports/offline/20261107-115609-505523`。
- 同一BGE重排改用GPU float32，没有替换模型或采用ONNX/低精度实验。原CPU经常超过5秒截止；GPU真实最终查询重排约318ms。与Qwen Embedding共用RTX4060，单次显存观察约6443MiB/8188MiB，不是并发容量验收。
- 最终检索探测：3个结果，backend=milvus，reranker=local，degraded=false，指纹b4316c98c5d7a549eb3471c10290e618f30c161f8614d56fa5f9baa2061cd796。详见`reports/latest-rollout/final-retrieval-probe.log`。

## 已启用的业务状态

常规业务服务已运行新镜像，恢复标准Uvicorn启动命令，不再挂载/执行临时模型出口守卫。`final-runtime.json`核验188份Python源文件零差异、16节点图、0012迁移、agent_tasks/agent_task_steps/退款preconditions列、任务循环及模拟退款预览开关均启用。24份当前发布文档全部采用新指纹与结构化策略，共1067片段。

真实HTTP冒烟最终5轮通过：订单+知识、同任务两轮排障、条件退款预览、明确取消；API任务查询和请求重放一致，未登记退款申请，合成账号已禁用，旧Token失效。保留前面5次尝试的失败记录，不计为成功。`runtime/smoke-report.json`的tasks=8包含该合成账号所有成功/失败任务，最终成功冒烟对应3个任务，不能将8个任务都记成成功。

启动模型出口沿用新增120次的v4原账本。先前61次行及报告不改，启动实际32次（包括失败），全阶段93/120，剩余27；usage未知0。启动子上限由24明确修订为36，未扩大用户授权的阶段总上限。原M4/M5/理解/旧117次任务阶段账本保持；`budget-closeout.json`逐项核对。

8个主栈及本地模型服务均healthy。现有业务订单、知识原文、历史版本和数据卷均保留；回滚材料保存在忽略的私有目录。Judge v1、process协调模式和原MCP不可用时进程内回退仍保留；没有启动未通过门槛的实验候选。Langfuse上报关闭，本地Trace继续工作。

最终全量源码回归已通过：`reports/offline/20261107-115705-429586`，**2235通过**、0失败/错误/跳过，464.76秒、12788条既有依赖/时间API警告；pytest与证据退出码0，起止来源稳定完整，blocked_network_operations=0。最终指纹`6f4e2a7beab1d4da3b5ffc3654166c3044ce076e1560e636e431822ca45b9c6d`，与运行镜像逐文件核验一致。

当前镜像digest `49c095c4a68b568b042093fbe98cca979eedd001286583011df27d9b885d644e`，标签`ai-customer:latest`及`ai-customer:latest-rollout-20261107`。`reports/latest-rollout/closeout.json`汇总最终部署、回归、索引、备份和额度；`final-runtime.json`、`final-health.json`保存实查结果。未提交整个既有脏工作区，未清理其他项目容器/卷。

真实业务并发/长期稳定性和独立人工语义金标不从5轮部署冒烟外推；process本地模型协调也不等于多worker GPU互斥。现有MCP依赖不可用时仍按约定回退进程内业务工具，不将本轮部署称作MCP协议完整接入验收。
