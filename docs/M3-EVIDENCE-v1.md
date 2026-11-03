# M3知识生命周期验收证据 v3

日期2026-11-03；基线a063d13，固定范围见M3-CLOSEOUT-v1.md。OPT-11全部12项及OPT-12正确性9项，共21项。实现及21项冻结验收完成：21/21项通过：包括用户部署的本地Ollama Embedding和Infinity Reranker真实依赖补验。供应商免费额度实验失败作为独立记录，不影响本地配置路径结论；该账号没有完成供应商Embedding接入。

## 实际交付

- 稳定document_id、独立不可变版本、CAS、SQL发布指针/epoch、原文位置/hash/章节/FAQ与表头上下文；相同标题互不干扰。移除旧手写DDL、MySQL按标题删除、失败却提前发布的旁路。0009只在合成库演练，历史正文/chunk保留，已知SQL别名可解析；旧未持久随机ID不能按标题猜回。
- 严格index映射/批数/维度/数值的Embedding；初始化不发付费探测；指纹新collection、数量校验、持久knowledge_index Job、租约/幂等/有限失败与管理员重试。保存不等于发布，新版失败保留旧版；取消发布不被迟到索引/重建/普通编辑恢复。
- 每请求SQL快照过滤发布/版本/删除，真实Milvus dense＋SQL快照应用层BM25，RRF融合；Embedding失败关键词仍工作。分数类型、候选/重排/证据数量、调用来源与当前耗时明确；缓存按epoch/配置隔离，生成及校验前复核版本。不是原生FULLTEXT，也不把排名当回答正确概率。
- 前端显示草稿/索引/失败和当前服务版本，编辑携带打开时的expected_version。业务权限仍为已认证用户共享读发布知识、管理员写，没有另定文档ACL规则。

## 测试层次与原始证据

T0：scripts/test_offline.py，临时工作目录、合成SQLite、禁网络，不碰业务数据。基线知识相关35项通过（reports/offline/20261103-171446-448324）。全量中间轮547通过（172845-205528），再一轮550通过（173851-853750）；先前全量554 passed（175446-899534）；最新全量556 passed、0 failed/skip、259.40秒、退出码0（reports/offline/20261103-230955-132771），包含SQLite迁移与真实权限。前次夹具导入显式导出，两文件12项独立运行通过（222249-353470），也已纳入最新全量。最后受影响82项通过（175349-071707），覆盖取消发布竞态、缺Milvus禁止发布、100chunk五批、真实Job消费/到期失败、RRF闸门和生成前版本失效。

C：tests/test_m3_embedding.py使用真实httpx请求编码/MockTransport供应商响应；100输入5批、乱序按index还原，缺项/重复index/布尔index/空/零/错维度拒绝且不污染缓存。此层不证明真实供应商可用。tests/test_m3_search.py覆盖Embedding超时、Milvus错误、Reranker池外结果、指纹/知识epoch缓存隔离及原文例外不误去重。

T1/F：scripts/setup_m3_test.py只创建label=aicshub.test.owner:m3-closeout资源；scripts/test_m3_real.py核对前缀/标签，清空Settings环境后禁真实模型，每例新MySQL合成库。Milvus2.5.10、pymilvus2.5.6、MySQL8.0及合成3维向量，不是模型语义质量。

- 首轮5项通过，reports/m3-closeout/real-20261103-172333，304.87秒。
- 迁移/实际双缓存读进程/索引退出补强5项通过，real-20261103-173005，57.13秒。
- 全套7项通过，real-20261103-173807，320.15秒，退出码0；双实际编辑进程20轮每轮仅一胜一409，服务实例读取新发布版；MySQL同名/失败/重试/重启与历史引用；实际os._exit(26)在索引前后，恢复同version不增加活动chunk；两个实际缓存读进程删除后都无旧内容。
- 发布事务与持有SQL快照的并发读补强2项通过，real-20261103-174523，41.73秒。读事务在epoch查询后暂停，另一事务切v2，原事务仍读完整v1，下个快照完整v2，没有混版。
- schema、index与逐路独有命中见reports/m3-closeout/milvus-schema-and-routes.json：关键词与向量路各一个独有候选，应用层BM25保留原词，不使用无法证实的旧FULLTEXT声明。

B：生产Vite构建、独立合成UI库，真实知识/认证/任务接口和独立worker，Embedding为明确标记的合成服务。前端7文件28项与TypeScript/Vite构建通过。实际浏览器：新增v1草稿→独立worker验证后发布；编辑同一document至v2→合成Embedding故障，页面显示失败且“当前服务v1”→管理员审计重试原Job→独立worker后显示“当前服务v2”。期间app真实进程57980→39400重启后，浏览器仍显示原文档及v2；后续39596启动成功。最后补做SQL/HTTP检查时Docker引擎与UI进程已不可用、MySQL拒绝连接，未把该最终重查记作通过；没有浏览器网络mock。

截图/AX：reports/m3-closeout/browser/failed-keeps-v1.png、retry-published-v2.png及同名txt；最新干净页面控制台error=[]。没有使用真实用户资料，也未操作业务知识库。

C/Local真实本地模型补验：用户确认已部署Ollama的Qwen/Qwen3-Embedding-0.6B与Infinity的BAAI/bge-reranker-large。Ollama实际返回两条合成文本及一条查询的1024维向量；写入新隔离Milvus collection；Infinity返回两条重排结果，provider=local。没有真实模型调用费、输入纯合成。原供应商授权另仅允许最多2请求/8条合成短文本/每条<=100字/关闭重试。可重用脚本`python scripts/test_m3_local_models.py`验证合成索引、dense召回、同维度revision隔离与Infinity重排，结果reports/m3-closeout/model/local-model-integration.json。业务路径随后真实返回hybrid、dense=milvus、bm25=sql_snapshot_bm25、reranker=local且活动version=2；非Mock ASGI接口与DB，外部模型调用0。应用重启后仍为1文档/2版本/2个历史chunk（活动发布版仅v2），HTTP历史版本与知识搜索通过。截图reports/m3-closeout/browser/local-embedding-app-v2.png，最新干净浏览器控制台错误为0。

另按用户授权运行供应商预算实验：最多2请求/8条合成短文本/每条<=100字/重试关闭。实际2请求、5条合成输入，两次HTTP400；第二次明确AllocationQuota.FreeTierOnly：免费额度耗尽且仅免费模式阻断。错误与输入数记录reports/m3-closeout/model/embedding.json。两次未取得供应商向量；没有切换付费模式、充值或再次请求。该供应商路径失败记录不被省略，但用户已部署且应用当前合成验收服务采用本地Ollama/Infinity，故AC由真实本地提供方完成。聊天模型调用0。

原记录：reports/m3-closeout/model/first-failure.json、embedding.json，退出码均1。第二次显式请求配置dimensions后仍被账号额度拒绝，不能证明供应商维度参数兼容。未改付费开关、未充值、未追加调用；费用/usage未回传，不推测实际计费。该外部供应商仍未验收成功；AC-11-08/09现已通过本地真实Ollama路径补齐，供应商失败不覆盖或替代本地通过证据。

## 逐项映射

| AC | 证据与断言 | 状态 |
| --- | --- | --- |
| 11-01 | T0 stable_id_restart_version_and_same_title_isolation；T1 MySQL服务重建、B真实app重启、历史版本GET | PASS |
| 11-02 | T0真实SQLite生命周期/API；T1 mysql_same_title_update_delete_restart_and_failed_index，统一SQLAlchemy仓储 | PASS |
| 11-03 | T0/T1同名文档稳定ID更新/删除，另一文档正文/版本/结果不受影响 | PASS |
| 11-04 | T0 API草稿/未就绪409/失败/原Job重试；B草稿→发布→失败保留v1→原Job恢复v2 | PASS |
| 11-05 | T0错向量/缺Milvus/失租不发布；T1索引写入异常，v1继续；SQL发布指针不变 | PASS |
| 11-06 | T1两个实际编辑进程20轮一胜一冲突；snapshot_remains_consistent_during_publish并发快照不混版 | PASS |
| 11-07 | T0 source_chunks全文覆盖、位置精确、<=400；FAQ长回答保留问题，表格保留标题/单位/表头，例外尾部可回查 | PASS |
| 11-08 | C乱序/缺项/空/错维度与dimensions编码通过；C真实本地Ollama返回1024维、batch/API index验证；T1新指纹collection Milvus写读；供应商额度错误另记录 | PASS（本地provider） |
| 11-09 | T0同维度revision改变拒绝旧dense；本地真实provider相同维度不同revision严格分collection，旧collection不可见 | PASS（本地provider） |
| 11-10 | T0 100chunk实际worker五批与SQL精确100计数，C 100输入真实请求编码5批；T1同version索引重试 | PASS（模型输入合成） |
| 11-11 | T0双服务热缓存发布/删除后新epoch；T1两实际缓存读进程删除无旧内容、编辑20轮新版本可见；窗口为下次SQL快照 | PASS |
| 11-12 | T1实际索引进程前/后退出并恢复同version；T0失租拒绝、到期最大尝试failed、上传超限无临时遗留、批量逐文件失败 | PASS |
| 12-01 | T1真实Milvus dense/SQL应用BM25独有命中各贡献候选，保存schema/index/routes；采用计划允许的应用层融合 | PASS |
| 12-02 | T0 Embedding失败/超时仍有BM25结果，显式degraded与dense空，模型故障不硬答无来源事实 | PASS |
| 12-03 | T0 Milvus错误/索引缺失走同指纹SQL向量、空route准确，未使用旧FULLTEXT接口 | PASS |
| 12-04 | T0真实RRF候选约0.0328通过RAG检索闸门，保留score_type/原始scores，不当概率 | PASS |
| 12-06 | T0 20候选重排可将第20个选入前3，recall/rerank/evidence独立，关闭重排也仅规定数 | PASS |
| 12-07 | T0前100字相同、尾部例外不同的两个chunk均保留，按完整ID/hash识别 | PASS |
| 12-09 | T0草稿/取消发布/删除/旧version无法进入缓存证据，生成前删除拒绝旧答案；API既有认证/管理员写约束保留 | PASS（既有共享读范围） |
| 12-11 | T0 epoch/Embedding revision/重排配置/数量变更后旧键不命中，热缓存仍查SQL，失效不依赖Redis扫键 | PASS |
| 12-13 | T0逐路ID/实际backend、encode/dense/bm25/fusion/rerank/总耗时、热命中本次耗时与meta/顶层统一 | PASS |

## 失败保留、未验证与停止点

早期本地失败是临时文件测试错误纳入同fixture的knowledge.db；修正为独立uploads目录后通过，没有弱化“不得留临时文件”的断言。原日志reports/offline/20261103-172705-201832保留1 failed/22 passed。真实供应商失败记录如上，未skip/xfail掩盖。

未运行：生产业务库0009迁移/部署/备份恢复；外部收费模型成功路径、外部收费reranker、生产真实语料检索质量/长负载；原生Milvus稀疏BM25不是当前实现。当前SQL降级投影最多10000活动chunk，超限明确错误；大规模预算须另验。AC-12-05/08/10/12的模型效果/覆盖校准按原计划留M4/M5，没有降低本阶段冻结条目。

供应商账号当前FreeTierOnly免费额度耗尽，导致该外部provider不可用；用户已部署本地provider且M3真实本地Embedding/Reranker及业务路径全部复验，故M3冻结范围内已验收。供应商失败作为配置边界记录保留。本轮代码/本地与依赖测试封存后停止，不自行切M4或改变供应商付费模式。原未跟踪三文件原样保留，不推送、不清空任何业务数据/知识/卷。提交/源码哈希/原始门禁和文件清单分别见Git历史、M3-GATES-v1.json与M3-FILES-v1.md。


提交：b11aea0范围；b2c87a2稳定版本/检索；467d400任务/API；40fd9e3前端；eaaa0e5取消发布/缺索引保护；8188b02维度/重排契约；075e01f真实依赖/模型预算/迁移权限验证。原始日志中的HEAD包含当时未提交工作区，不能把测试结果冒称历史HEAD干净构建；最终代码提交与源码哈希已另记于门禁。


最终核对：初次Docker启动遇到残留runtime端点，备份该端点后恢复引擎；仅启动本次用户要求的本地Embedding/Reranker与M3专属合成依赖。AssetLens等其他Compose项目恢复并显示healthy；没有删除/清空任何卷。原始T1容器固定宿主端口冲突后，新建了同label隔离临时MySQL/Redis容器。最后代码未使用导入清理一度移除了跨文件pytest夹具导入，已通过同名显式导出恢复；两文件单独运行12项通过。本地模型验收提交e676fd8；最终文档提交见Git历史与交付报告。最新全量556项、前端28项与生产构建在本地验收服务绑定后复跑通过；最后仅补强验收脚本对实际测试Milvus容器label/宿主端口的核对，再运行真实本地脚本通过。产品代码未新增变化。


本地Embedding真实完成后，将合成UI服务切换为正规的EmbeddingService配置：新增测试文档v2经持久Job及独立worker写入独立合成Milvus collection并发布。合成客户真实认证接口检索返回hybrid，dense backend=Milvus、keyword backend=SQL snapshot BM25、reranker=local，来源version=2；app进程重启后SQL为1文档/2版本/2个历史chunk（活动发布版仅v2），历史v1可读、当前仅v2命中。最终已重启Docker并恢复原专用合成MySQL/Redis及新建同owner临时替代容器；该操作未连接/修改业务数据卷。证据reports/m3-closeout/model/local-app-search.json及browser/final-restart-check.json。生产UI控制台原测试构建的旧动态chunk error在初次验收加载发生；硬刷新后当前Production构建成功加载，登录/索引/检索流程通过。


本轮本地启动说明：Docker Desktop启动时旧run/dockerInference端点不可访问，保留为Docker/run.stale-*目录后重新建立runtime并启动；没有factory reset、卸载或删除模型卷。仅显式启动embedding-local/reranker，复用已有Ollama及HuggingFace缓存。Docker自动恢复其他项目restart策略的容器，AssetLens显示healthy，未对其执行迁移或改配置。M3旧tmpfs测试库随Docker中断不再保留，原容器仍保留；旧端口被Windows拒绝绑定后新建同owner隔离资源，本次UI生成了全新合成库，不能把它说成恢复了上轮tmpfs数据。该新库的app进程重启保留1文档/2版本/2chunk已单独验证。

本轮初始验收适配器误将APIEmbedding当作EmbeddingService导致合成v2索引失败；旧v1仍发布，随后改用正规包装，仅在验收进程设本地地址/模型/维度，管理员重试原Job后成功。产品配置.env仍指DashScope，没有把启动本地模型说成业务主栈已经切换。本地脚本首次导入/旧端口问题修正后真实运行退出0；使用专属label+实际绑定端口防止误用业务Milvus。

最新干净浏览器截图local-embedding-app-v2.png显示当前服务v2，控制台error=[]。旧标签页早期动态chunk加载失败保留记录，刷新/新标签后实际流程通过；没有把失败尝试算作完成。
