# M5 实施与验收证据 v1

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
