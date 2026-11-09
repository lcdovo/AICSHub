# M5 实施与验收证据 v1

2026-11-05后续裁决完成：用户要求“全部都由你判定”，48项已在独立v2包落地，616题统一assistant_reviewed、原595条ID及24条人工历史保留，无需逐题确认。142相关T0通过；词法Recall@20=1.0、首位341/351，不代表真实模型质量。详见M5-ADJUDICATED-v2.md。以下为历史记录。

2026-11-05最新安排：用户已委托助手代复核剩余材料，无需逐组用户确认。571题助手复核完成：523条与材料一致、48条待修订/澄清；原24条人工裁决不变，未增加人工金标或独立质量PASS。详见M5-ASSISTANT-REVIEW-v1.md及修订队列。

2026-11-05最新人工裁决：打印机6题由用户确认全部通过，累计24/595题（显示器、扫地机、投影仪、打印机），其余571题待审。四份裁决绑定原材料hash；整体留出/全局语义隔离/实际输出论断仍未通过，性能优化继续暂缓。

2026-11-05质量基线：18条人工已审题在55份合成文档的BM25排序中相关证据首位18/18，Recall@20/MRR/nDCG@5均1.0；仅3个主题词法基线，不是全量质量验收。111项相关T0通过，生成回答/模型调用0/0，性能按用户要求暂缓且未通过。详见M5-QUALITY-BASELINE-v1.md。

2026-11-05当前安排：用户要求先跳过重排性能优化，原失败与门槛保留、未标PASS；工作转入M5质量。显示器/扫地机/投影仪共18题已明确人工批准，剩余577题待审。整体留出、全局语义隔离及实际输出论断仍未通过。

2026-11-05图融合补验：72相关T0通过。BiasGelu24/SkipLayerNormalization48实际融合、Attention0/24；候选数值11/12通过，load-003最大logit差0.0390625超原容差，已停止加压，候选压力与A2均NOT_RUN。原版A1并发10 P95=2875.694ms，候选不采用、M5不通过，详见M5-ONNX-FUSION-v1.md。

2026-11-05最新进度：显示器、扫地机两组各6题已由用户明确批准，累计12/595；其余583题待审。裁决按原材料hash分别保存。整体独立留出、全局语义隔离及实际输出论断尚未通过。

2026-11-05更新：后续GPU ORT镜像、小图及FP16模型导出已成功；12条数值对照通过、top1一致12/12但完整排序9/12。原200条ONNX直调全部完成，并发10 P95=2800.145ms仍超2000ms，不采用候选；CPU形状/标量运算及失败记录详见M5-ONNX-VALIDATION-v1.md。人工复核显示器与扫地机共12题已通过，其余583题待审，整体留出/输出论断仍未通过。以下历史记录保留。

ONNX可行性后续：94相关T0通过，断网/只读实际预检BLOCKED_GPU_RUNTIME（缺CUDAExecutionProvider，TensorRT未安装），报告reports/gpu-pair/onnx-preflight-20261104-231059/report.json；torch GPU小图通过不代表ORT模型推理。GPU版wheel下载未完成，人工中止且无候选镜像，ONNX模型导出/推理/压力NOT_RUN；固定镜像/源码指纹/构建失败与内存边界见M5-ONNX-PREFLIGHT-v1.md。

注意力候选后续：78相关T0通过，原生SDPA/FP16实际加载核验通过；batch4单变量与batch8组合两组ABA均完整、有效1200请求全完成，候选并发P95=2058.153/2138.241ms仍FAIL。保留排序/四位小数分值变化及早期UTF-8/清理失败，不采用业务候选；详细源码版本、原始报告与边界见M5-ATTENTION-PRESSURE-v1.md。

微批准入下batch4/8/4压力后续：63项相关T0通过；三轮各200条全部完成、BUSY0，完整排名200/200保持，但batch8并发重排P95=2374.700ms仍超原2000ms，前后batch4为2807.103/2910.699ms。总报告reports/gpu-pair/batch-pressure-20261104-200603/report.json；源码稳定完整，生产Python本片未变，不采用batch8、不切业务，原容器恢复后已停止。详情及范围见M5-BATCH-PRESSURE-v1.md。

本片最终全量T0：reports/offline/20261104-195346-695223，1105通过、0失败/错误/跳过、530.69秒；源指纹168e1ffe9c617351a3742d8653423ea061b05b04980fd8eeb13b4dd7b2ba1858与最终压力一致，起止稳定完整且禁网络操作0。仍不填补真实质量/容量门禁。

跨请求候选后续：已实现单进程Embedding微批、逐请求Permit、独立公平模型通道与派发前失效检查，102项相关T0通过。最终原200负载串行/并发10均100/100完成、BUSY0；并发重排P95=2970.429ms、总P95=3026.793ms，原2000ms门槛仍FAIL。默认process、业务路由及供应商660/660保持；本次恢复的两个候选模型容器已停止。源码/报告与早期失败、计数修正及适用范围见M5-CROSS-REQUEST-BATCH-v1.md，不当完整M5或生产容量通过。

固定到达率/batch后续：新增有界open-loop负载与候选自动恢复工具，61项相关T0通过。相同100条负载/配置，batch4→8→4三轮，1/2/3请求每秒各20/20完成；4请求每秒17/19/18完成，6请求每秒13/14/12完成。其余均BUSY，不将小样本通过当长期QPS；batch8不采用、原候选已恢复，完整M5容量/质量仍未过。来源与局限见GPU-ARRIVALS-BATCH-v1.md。

常驻生命周期后续：04ab213新增成对对照/最多2路provider诊断，随后APIEmbedding客户端首次创建/关闭引用/本地代理/原截止修复，157项相关回归通过。真实常驻原200负载串行100/100、端到端P95=425.817ms、重排381.458ms，但并发10仅2/100完成、98BUSY，整体FAIL。详情/源码指纹/局限见EMBEDDING-HTTP-LIFECYCLE-v1.md，不当完整M5或容量通过。

2026-11-04后续释放补偿切片：135项相关T0、11项真实Redis通过，故障释放最多两次同owner条件命令，显式区分released/not_owner/unknown。候选恢复后原200压力串行100/100、重排P95=1118.007ms，但并发10仅1/100完成、99BUSY，整体FAIL；源码起止稳定完整。实际容量与质量未通过，不切业务；报告/限制见LOCAL-LEASE-RELEASE-v1.md。

2026-11-04。M5总体INCOMPLETE，M4实体质量前置仍FAIL；供应商660/660不变。标准及26项原断言见M5-PLAN-v1.md、M5-GATES-v1.json。以下有限回归不代表独立留出质量或真实模型通过。

## M5-01 所有回复共用发布底线

OPT-13，AC-13-11局部、关联AC-10-08/13-08。新增publication_guard，工作流/缓存/生成回复及服务端附加引用在发送token和写回答缓存前都检查已识别的电话/邮箱/地址/密钥泄露、虚假真人接通与预计排队时间。只输出错误码，不把敏感草稿放入审查报告。拦截后发布固定安全通知，ReviewResult=false、不可缓存、无自动人工或动作重执行。已存在真实SQL业务不撤销；本闸门不证明其完成。技术“等待3分钟再重启”和订单送达日期保留，不把所有时间数字当排队承诺。

先红：reports/offline/20261104-102805-166159，28失败/6通过，复现四类workflow直接发布危险草稿。修复初验95通过（102839-711082）；增加生成回复出口和合法技术时间/日期反例后，最终98通过（reports/offline/20261104-102919-774119）。包括37项新测试及人工事实/轮次/独立响应组件回归，T0临时SQLite、禁止网络、合成模型；compileall和本切片Ruff F401/F821通过。未知表达/无标签地址/所有注入变体等不在此确定性规则保证中，完整AC-13-11与质量误拒率仍待验。

## M5-03 Judge未知失败关闭与共享证据

OPT-12/13，AC-13-06/09、关联AC-12-08/12。Judge关闭、无服务、None、结构非法、异常/10秒超时返回support=unknown、fact_score=null、明确拒绝未核实政策。结构契约严格必填、extra forbid、禁NaN；独立响应出口也核验替换Judge，grounded=false即使0.99也不能通过。CancelledError继续传播。支持supported/partial/unsupported/unknown，不以召回排序得分当支持概率。

生成和Judge均使用format_review_evidence的完整JSON片段（含ID/version/完整条件）与完整成功工具回执；删除Judge前三片段/工具300字符及生成前三片段的不同裁剪。网关若因上下文/预算拒绝仍unknown，不退回低标准。缓存命名空间升级validated-v3，不复用此前Judge未知也可能放行的旧缓存；没有删除旧业务缓存或数据。

先红12失败（reports/offline/20261104-103055-849846）。中间回归78通过/6失败（103152-059953）：5项新测试错误地假定旧rag_state一定含类型bundle，改为检查实际llm_judge支持结论；1项旧计量正向夹具Judge=None与新安全契约冲突，改为明确有效正向Judge，并完整保留“Review通过但无效计量仍不缓存”断言，负向None独立保留。最终85通过（reports/offline/20261104-103254-017388），含13项新回归、轮次/证据版本/模型重绑定/计量/响应装配，T0合成替身，无真实模型质量证据。compileall及本切片Ruff通过。新Judge提示/schema未做供应商兼容或独立人工标注质量验收，不宣称AC-13-06的L层/整体M5已通过。

## M5-02 固定关键事实反例

OPT-13，AC-13-01/02/03/04/05/08/10局部。FactValidator不再检查最后成功字典的字段复述率，改查全部成功对象回执中的订单状态/金额/币种和同订单冲突；临界事实错误直接critical拒绝，不以其他分数抵消。模拟产品不接受真实支付/到账成功，缺少申请ID及有效状态不能宣称申请已登记。额外赔偿/终身承诺保守拒绝；此固定规则不是通用语义蕴涵，完整承诺条件需独立质量审查。多对象分句、复杂否定和金额类型仍需反例扩充，不能外推为任意自然语言可靠。

最终发布底线也执行上述事实规则，workflow标记不能绕过，拦截不重做动作。SafetyValidator不再仅因诈骗/赌博等名词出现而拒绝防骗建议；简短回复只给完整性提示，不因长度强行拒绝正确事实（如“已发货”）。原事实/安全阈值未降低。

先红15失败/4通过（reports/offline/20261104-103504-169965）。中间106通过/1失败（103538-228123）暴露旧完整性长度惩罚造成短正确回复误拒，改为非阻断提示后107通过（103559-916487）。接入最终出口后163通过/1失败（103640-099917），旧正向REST/WS退款替身仅ID/金额无状态，被新检查拒绝；两个正向替身补充pending_review/CNY/模拟标记，保留消息数量、一次调用、R1回执和token一致断言，另新增部分回执负例。中间第一次只改到同文案另一夹具仍164通过/1失败（103728-570509），失败保留；最终165通过（reports/offline/20261104-103816-882970），含23项新固定事实/安全/外发反例及先前工作流/Judge/退款确认回归。compileall、本切片Ruff通过；T0临时SQLite/替身，不是独立人工反例质量率或真实支付验收。

## M5-04 正确的指标与失败关闭报告器

新增scripts/evaluate_m5.py，按chunk+version精确相关标签计算Recall@20、Evidence Hit@20、MRR、分级nDCG@5，必要条件覆盖要求所有必需证据/条件。缺观测/错误保留分母，失败反例不能当正确拦截；无独立人工输出标注时论断支持率null；重复ID/重复查询、NaN延迟、未知/重复结果、数据hash不符及不来自排名结果的生成证据拒绝。CLI可检查开发/留出case/query/近义cluster隔离。缺任何规模/层次/版本/人工标注均INCOMPLETE，指标失败FAIL，不以空集均值0或模型自评分冒充PASS。

acceptance/m5冻结37条合成开发夹具和12份合成知识及hash，明确assistant_draft，不冒充所需560条独立质量留出。独立留出/人工输出论断标签尚未完成，是M5质量验收真实缺口。即便未来单次报告通过也仅PASS_SINGLE_RUN_ONLY，全部消融/冷热、M4前置另验。报告器16项手算/边界专项连同M3检索生命周期共36通过（reports/offline/20261104-104415-379774），compileall/Ruff通过。文件见acceptance/m5/README.md。

## M5-03补充：内联引用身份

OPT-13，AC-13-07局部。新增citation_guard核验[证据:chunk_id@version_id]（亦支持引用/source/evidence标签）的当前EvidenceBundle身份，缺失ID/错误版本/格式不全直接拒绝；即使独立Judge声称通过也不能放行伪引用。所有workflow最终出口同样检查。存在的引用不证明论断支持，仍需Judge核对完整条件；未声明格式的任意自然语言引用不在该身份解析保证中，完整L层仍未验。

6项新身份/独立Judge反例及相关回归57通过（reports/offline/20261104-105427-649307），compileall/Ruff通过。首次新测试漏注入知识快照检查器，误连未建表的临时SQLite，52通过/5失败且临时文件句柄清理报错（105338-738454）；改为明确快照替身后重跑，未关闭真实版本检查或放宽断言。T0合成，无真实模型引用支持率证据。

## M5真实依赖及本地开发实验（进行中）

用户显式允许启动Docker Desktop。本机安装位置D:/develop/docker/program，经安装登记核实后启动；只显式启动test.owner=m3-closeout的原隔离栈。未执行业务Compose up、数据删除或部署。两个历史测试Milvus共享etcd root协调键，均因indexcoord CompareAndSwap冲突以134退出；首次4个T1用例setup连接失败（reports/m3-closeout/real-20261104-104606），不能当业务断言通过。保留原容器/数据，停额外本地模型测试Milvus，仅启单个隔离Milvus后，4项检索/发布用例通过（reports/m3-closeout/real-20261104-105816），包括真实dense/关键词独有命中、同名更新删除重启与索引失败、双读者缓存排除删除、发布快照一致。该旧执行器尚无开始/结束来源清单，本次按当时源码和报告解释，不冒充有完整指纹的T1版本验收。

真实MySQL/Redis REST/WS退款与注入证据不能改变身份/确认2项通过（reports/m1-closeout/real-20261104-104606）；显式reuse-m3，owner/运行端口核验，source_tree_stable/source_snapshot_complete=true。所有SQL为新建合成case库，未用业务库。已有服务随Docker自身重启策略恢复，不调整其他项目配置。

本地Ollama/Infinity模型清单实际可读。首次开发消融仅2/12样本（reports/m5/local-20261104-110128/report.json），12合成知识、新Milvus collection及临时SQLite；Embedding方法18次，无外部/聊天调用。BM25/dense/hybrid两样本Recall/nDCG=1仅作诊断；实际Reranker两次5秒截止后保序回退，不能称真实重排通过；执行器Windows临时目录退出时PermissionError，整轮FAIL，所有结果保留。单独本地服务12文档调用成功/12结果/4219.736ms（reports/m5/reranker-12-provider-probe.json），仅证明服务能力。后续实验以单进程30秒有界超时运行，业务默认5秒保持，不把实验超时调整当业务配置完成。

完整T0初验959 passed/2 failed（reports/offline/20261104-110236-427000）：正常/损坏预览两种已提交退款恢复失败。根因恢复文案用完整audit.result_payload，但给审查器仅refund_id，M5新回执检查拒绝缺状态的“已登记”。不表示重复退款或SQL提交丢失；当前修复将同一完整审计回执供审查，回执要求不降低，后续复验单列。探索中临时缩窄require_receipt的路径已放弃，相关失败报告111147/111240（106通过/3失败）保留，不计成功；最终不提供恢复绕过开关。

用户已确认独立人工复核由其负责，仅确认分工，没有确认任何标签。M5-REVIEW-QUEUE-v1.md已整理12份合成原文和37条开发候选，全部待审。独立560条留出及其输出论断尚未完成，M4质量前置和新增真实聊天/Judge额度也未解决，M5完整验收保持INCOMPLETE。

## M5恢复回执复验

最终修复仅将refund恢复审查证据从ID投影改为audit.result_payload完整回执，与生成模板来源一致。没有require_receipt豁免开关、未缩小回执检查范围。原M2正常/损坏预览恢复测试保留一次申请、两条消息、0.10精度、模拟说明与清理待确认断言，仅追加失败诊断error_code。新增部分成功回执workflow流式拒绝反例；非workflow路径本会在validate_candidate拒绝后返回人工节点，新增测试明确workflow=true检验最终发布底线，未改变生产路由来迎合测试。

最新受影响110 passed（reports/offline/20261104-112252-390635），含M2全部Run、M5事实/工作流、聊天轮次。临时探索两次3失败和一次新增用例误判返回节点1失败保留；最终原3项ResponseValidator缺/部分回执断言恢复不变、原回执要求完整保留。compileall/Ruff通过，后续重跑全量；本次属于恢复数据一致性修复，不新增退款执行或支付能力。

## M5-05 本地开发消融

OPT-12，AC-12-05/13/14局部。KnowledgeBaseService.search显式retrieval_mode=hybrid/bm25/dense，仅进程内评测接口，HTTP和聊天默认hybrid不变；关闭路线不执行模型或BM25评分，缓存键含mode防止混用，未知mode先拒绝。显式BM25不把按要求关闭dense虚报为依赖故障；其他真实故障/默认路径降级保持。rerank实验通过RAG_USE_RERANKER仅改实验子进程设置，源码业务默认预算未改变。3项路线/缓存专项与M3/指标共39通过（reports/offline/20261104-112400-567466），compileall/Ruff通过。

scripts/evaluate_m5_local.py清除宿主业务配置、工作目录临时SQLite、核验m3-closeout实际运行端口、新m5_dev随机collection，loopback模型固定，无聊天/Judge/付费调用。真实模型qwen3-embedding:0.6b/1024维/revision ollama-ac6da0dfba84和BAAI/bge-reranker-large，12份合成知识索引、12条开发查询、4种路线各cold/hot共96观测。新报告reports/m5/local-20261104-111552/report.json为PASS_LOCAL_DEVELOPMENT_ONLY，真实local重排均成功，Embedding方法48次，source_tree_stable/source_snapshot_complete=true，源码指纹bd31d9557bd367edd6bf270c13a1f24542478327ce356138f186aa1be9f5c731。方法计数不是provider精确请求账单，批次/缓存及重排请求另据实际metadata解释；外部与聊天调用0。

| 路线 | 开发Recall@20/nDCG@5 | 冷P95毫秒 | 热P95毫秒 |
| --- | --- | ---: | ---: |
| BM25 | 1.0 / 1.0 | 12.3 | 12.3 |
| dense | 1.0 / 1.0 | 991.5 | 10.7 |
| hybrid | 1.0 / 1.0 | 789.1 | 12.8 |
| rerank | 1.0 / 1.0 | 12929.4 | 21.1 |

全部热次命中本测试实例缓存；冷次清本实例检索与文本缓存，不清业务Redis。语料仅12chunk，Recall@20可包含全部语料，1.0不证明大库召回质量；必要条件覆盖/无答案/真实回答支持没有用子串猜测填分。实验进程供应商上限30秒，业务默认5秒确已在前次真实链路回退，故不能标AC-12-05/14完整通过；改写消融、200独立运行/并发负载、真实聊天/Judge以及独立留出标注都未完成。Windows cleanup错误修复为先退出临时工作目录再销毁；未删除业务数据，新增测试collection保留不清理。

本报告之后的显式BM25降级metadata修正仅39项T0复验，未声称其改变前一份真实来源指纹；实际本地推理结果按上述原指纹解释。

## 综合回归中的截止时间用例校正

本轮第二次全量961 passed/1 failed（reports/offline/20261104-112502-003338，549.32秒），失败为原test_stream_uses_remaining_run_deadline_and_records_unknown：先把整轮剩余设置50ms，再执行实际SQLite SQL预留，负载下SQL已超过截止，生产正确not_sent，测试错误期待unknown。没有放宽生产超时/计费逻辑，也不把not_sent或unknown混为成功。

测试现在真实执行SQL预留后再冻结供应商剩余500ms，替身用Event确认实际进入astream，并等待至生产截止取消，必须unknown/unknown_reserved；另加派发前过期用例，必须零供应商调用、not_sent、input/output/预算tokens和cost均0。原未知保守计量断言完整保留，新增边界分别核验。网关/迟到/恢复/Judge相关45通过（reports/offline/20261104-113522-202851），compileall/Ruff通过。最终全量后续记录，不删除两个历史全量失败。

## 本轮最终证据与剩余验收

最终全量python scripts/test_offline.py -q tests --tb=short，963 passed/0失败/0错误/0跳过，415.49秒（reports/offline/20261104-113638-736165）。source_tree_stable/source_snapshot_complete=true，起止指纹01bc7de693aeb14e095b13f52d4528a9efb628e780f3cd1dc2556f3fd1d28e07；网络禁止且blocked_network_operations=0，未调用付费供应商。已有依赖/datetime弃用警告9881条如实保留。compileall与切片F401/F821通过。

最新真实MySQL/Redis2通过（reports/m1-closeout/real-20261104-112538）及MySQL/Milvus4通过（reports/m3-closeout/real-20261104-112538）。M3外包装保存m5-source-start/end及m5-provenance补足旧执行器来源，两报告源码指纹c2f2d6dbfbf6bfc926c10f1a1314457e4c3205452be18a6284599ffd1bd93ade，均稳定完整。此后生产未改，网关测试独立调整；局部T1不替代全部真实集成/质量验收。

正式质量留出候选已新编制：595条，其中330可回答（55主题簇/每簇不同字段或行为问题）、60无答案/部分答案、100反例、105正确候选。合成语料55份，业务知识库不写入。docs/M5-HOLDOUT-DRAFT-v1.json、CORPUS/SOURCE/MANIFEST-DRAFT及12批docs/m5-review/batch-*.md已准备；标签仍assistant_draft、reviewer=None，用户只确认复核分工，没有确认标签。程序检查595schema、chunk/version存在、ID/query/cluster与开发集精确无交集、hash一致；这些不能证明语义独立，仍须用户核查近义重复、允许事实、例外、草拟反例是否真错，之后冻结数据。未调用模型对该候选评分或据其结果调参；实际输出论断支持标签尚未产生。

595候选保存在docs待审，不改本次全量来源清单；后续仅更新acceptance/m5/README.md说明及dataset-manifest.json的候选资料指针，无生产/测试Python改动，元数据/候选完整性另验。M5-ACCEPTANCE-v1.md结论INCOMPLETE，26项原断言与195项总索引保留；M4实体失败、独立人工标签/真实聊天Judge质量、改写消融、业务默认5秒重排预算和综合负载仍阻止完整通过。供应商660/660、rules/灰度0保持。全部独立提交、不推送；不进入M6或部署。

## M5资源对照补充

CPU线程2/4及batch2/4/6/12服务级重复对照、GPU透传以及正确/作废报告的严格边界见M5-RERANK-BUDGET-v1.md。隔离GPU容器每轮只收12条合成候选；两轮应用开发消融确认新reranker-base目标、真实运行模型、固定CPU两线程并行与完整观察。评测入口拒绝非loopback地址；53项回归通过。CPU/GPU容器临时启动并stop，不移除容器/卷、不改业务服务设置。

## 后续完整候选/重排预算切片

见M5-RERANK-BUDGET-v1.md。新增本地BGE4条/批、进程共享最多2批并发、完整全池评分/坏批次整体回退、父5秒绝对截止及HTTP剩余预算、过期停止后续provider、超24不静默截断；外部单次调用及其他本地模型保持原请求粒度。metadata/缓存纳入算法版本与阶段预算。本地评测器改默认5秒，显式较长预算和canonical→实际标签映射记录。

17项新专项及受影响回归107通过（reports/offline/20261104-120133-332724），源码起止a4fa113f5078604ebd8d78a194c1b970ec68f3d45b2a17bd0f75f95e833a93bc，稳定完整，网络禁止且blocked_network_operations=0；编译/静态检查通过。此次仅受影响范围，963全量属于此前版本，不冒称当前全量。

真实默认5秒实验reports/m5/local-20261104-120224/report.json同一源码指纹、稳定完整：12冷+12热观测，冷8条local、4条超时rank_preserved，热均缓存命中；全冷重排阶段P95=5005.732ms、端到端P95=5989.6213ms，仍不满足原2秒候选目标，整体FAIL_LOCAL_RERANK_FALLBACK。语料小而Recall/nDCG=1不代表真实重排或独立质量通过；没有剔除失败、放宽截止或模型质量标准，未操作595候选调参、业务数据/配置/部署，外部/聊天调用0。M5性能和质量缺口继续保留。

资源对照最终端点回归复验53 passed（reports/offline/20261104-134155-158519），compileall/Ruff/diff检查通过。只修改评测工具/测试与证据文档，未修改生产Python或部署。

## 双GPU候选部署核验

详见GPU-PAIR-DEPLOYMENT-v1.md。复用已存在owner=gpu-model-switch候选，固定镜像独立Compose清单通过配置检查但未重建现有容器。60项T0及12查询真实双GPU链路、4轮同时推理均完成；真实重排P95 2713.665ms未达2秒，Embedding同digest GPU/CPU相似但不完全等同，业务路由与旧索引保留。不得称完整部署切换或M5通过。

## 双GPU切换前有限压力/索引对照

新工具scripts/verify_gpu_pair.py只核验并访问本机候选端点；200条不同合成压力请求不作为独立语义质量样本，12语义簇、每次20条长候选、并发1/10各100次。完整report在reports/gpu-pair/acceptance-20261104-153203，FAIL：串行96/100、并发10为57/100成功，重排P95=3376.825/5015.470ms，47次ReadTimeout均保留；显存采样max6805MiB，未观察到容器OOM杀死/重启。12文档12查询CPU/GPU隔离collection混合top1/top3保持，但2次完整排序变化，仅作观察不批准业务混用。细节、失败和后续边界见GPU-PAIR-ACCEPTANCE-v1.md。15项新报告/回包/工作负载/资源守卫测试连同相关回归75通过，生产代码不改，未重跑全量。原M4/M5门禁及660/660费用保持，未切业务路由或删除任何业务数据。

## 本地模型共享预算与背压切片

详见LOCAL-INFERENCE-BUDGET-v1.md。HTTP出口单进程active1/waiters2、前台500ms/后台50ms，继承阶段5秒截止；后台忙时持久Job有界退避，旧发布指针保持；查询忙/超时不缓存，证据不足时提示真实繁忙/超时且不隐式转人工。跨worker及服务端CUDA取消不在保证内。1024项全量T0在缓存收口前通过，缓存收口后161项受影响回归通过；两份来源分开记录。新真实200次同负载串行100/100，并发10仅5完成/94BUSY/1deadline，整体FAIL，不能将拒绝当成功；后台额外单次真实GPU+临时SQLite探测retrying且旧版不变。生产GPU路由和数据保留，M5质量性能仍不通过，无付费新增。

## 整轮准入与Redis协作候选

详见WHOLE-REQUEST-ADMISSION-v1.md。冷检索/索引批次整轮Permit、独立模型入口协作、旧BGE子线程继承、在途引用延后释放、失租拒绝返回、同token重放幂等及未知回包条件清理已实现；默认仍process，Redis模式只在专用测试库/随机键启用。真实Redis7场景通过，全量1038 passed（reports/offline/20261104-164455-476590），同源码快照稳定完整。旧重放缺陷先复现、旧全量中止及两轮压力失败全部保留。修复后真实200压力串行24完成/70BUSY/6deadline，并发10仅1完成/99BUSY，拒绝前模型派发均0；整体FAIL，不能用Redis互斥或快拒绝宣传容量提高。M4/M5质量/性能及业务切换仍不通过，无新增付费调用或业务数据删除。

2026-11-05最终全量T0：1195 passed、0失败/错误/跳过、246.48秒，10009条既有依赖及datetime弃用警告。报告`reports/offline/20261105-000417-430263`，源码指纹`143c7f6599015f930987c662199699bf7e24d47682983ac06698b6ac126155dd`起止稳定完整、blocked_network_operations=0。包括本轮查询改写修复、ONNX工具及之前已有未提交批处理实现；不是纯已提交HEAD、模型质量或容量/部署验收。

## 2026-11-05 v2生产组件离线诊断与P0修复

详见M5-OFFLINE-CHAIN-v1.md。351题经临时SQL发布与生产BM25冷/热检索，各351条Recall@20=1.0、nDCG@5=0.988366，前5条必要条件原文可得；205固定候选暴露3条P0漏检，新增反例先复现18失败，修复终身修饰词、模拟支付完成及无依据权限变更声明后20/20固定P0阻断、105/105正确候选确定性接受。80条政策语义反例仍需独立Judge；Judge不可用时180条政策候选全部拒绝，其中100条正确候选属可用性损失，不当质量PASS。报告reports/m5/offline-chain-20261105-094341-374083，稳定完整源码bde4254d78b1f42bbf33760cad2ba50d30839b85fa180151c3b377ccaff0762c，网络0、模型0。材料/阈值不改，M4/M5质量与性能门禁保留。

最终版本全量T0：`python scripts/test_offline.py -q tests --tb=short`，1314 passed、0失败/错误/跳过，406.70秒，10159条既有依赖/时间API弃用警告；报告`reports/offline/20261105-094341-313618`。源码起止指纹`bde4254d78b1f42bbf33760cad2ba50d30839b85fa180151c3b377ccaff0762c`稳定完整，与最终离线诊断同一来源快照，blocked_network_operations=0。包含既有未提交实现，不是纯已提交HEAD，也不证明真实模型、MySQL/Milvus/Redis、浏览器或部署验收。编译、切片Ruff和diff检查通过。

2026-11-05真实生成/Judge执行器准备完成，详见M5-LIVE-READINESS-v1.md及M5-LIVE-PLAN-v1.json。新增evaluate_m5_live.py/m5_live_budget.py及预算回归，79相关T0通过；616题/1027次SDK替身演练完成，真实调用0。无新明确额度时真实入口已验证拒绝，原660/660账本不变；计划仍PROPOSED_NOT_AUTHORIZED，M4/M5质量与性能门禁不升级。

2026-11-05用户授权真实诊断已完成：1027/1027请求、616题均有结果，原M4账本不变。固定反例6/100放行（确认5/争议1，冻结标签不改），超过2%门槛；105正确候选误拒0、20固定P0误放0。411生成输出由助手复核，非独立金标。另修复支付话题误当付款事实，209相关T0+10审计测试通过，616冻结Judge离线回放仅noanswer-046从误拒变接受，新调用0。真实源码与后修复来源分开记录，详见M5-LIVE-EVIDENCE-v1.md。新旧额度均耗尽，M4/M5整体及性能未通过；不发布/部署。

Judge v2分步离线候选：新增judge_contracts.py、v2契约/响应集成/5条已见范围正反夹具；45→102→248相关回归分阶段通过，最终全量另记。616历史v1判决禁网络回放与原离线结果全部一致，原报告/付费账本未改，新增付费0。默认v1；手工scope问题被拦不等于模型会识别，历史6%误放不提升。详见M5-JUDGE-CONTRACT-v2.md。

最终版本全量离线验收：`python scripts/test_offline.py -q tests --tb=short --disable-warnings`，1418 passed、0失败/错误/跳过，190.81秒，10131条既有依赖/时间API警告；`reports/offline/20261105-235616-879886`。源码起止指纹`ae8eb9f9c1f000c37883a9680ba821ef07e946b6168ea74b27fcaddc3c701640`稳定完整，与最终616回放同一来源快照，blocked_network_operations=0。包括本轮及此前未提交实现，不是纯已提交HEAD；原真实报告/数据/新旧费用账本hash复核一致。编译、切片Ruff与diff检查通过。此为全量T0，不证明v2真实供应商质量或完整部署；业务默认v1，新增付费0，原M4/M5门禁不升级。

2026-11-06用户确认256次/输入预留400万/输出262144的新Judge v2方案，256/256已完成。新反例100零放行、新正确100零误拒，但20边界回复仅14允许、6契约/绑定未知；运行期间graph.py/intent.py并行变化，整体来源验收FAIL，不启用业务v2。新增错误码和下一派发前源码检查后121相关T0通过，新增调用0；旧报告/账本不改。详情M5-JUDGE-V2-LIVE-EVIDENCE-v1.md，全部新旧费用额度耗尽。
