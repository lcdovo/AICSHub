# M3知识生命周期验收证据 v1

日期2026-11-03；基线a063d13，固定范围见M3-CLOSEOUT-v1.md。OPT-11全部12项及OPT-12正确性9项，共21项。实现完成；19项按冻结层次通过，AC-11-08/09的真实供应商部分未通过，M3不标整体验收通过。

## 实际交付

- 稳定document_id、独立不可变版本、CAS、SQL发布指针/epoch、原文位置/hash/章节/FAQ与表头上下文；相同标题互不干扰。移除旧手写DDL、MySQL按标题删除、失败却提前发布的旁路。0009只在合成库演练，历史正文/chunk保留，已知SQL别名可解析；旧未持久随机ID不能按标题猜回。
- 严格index映射/批数/维度/数值的Embedding；初始化不发付费探测；指纹新collection、数量校验、持久knowledge_index Job、租约/幂等/有限失败与管理员重试。保存不等于发布，新版失败保留旧版；取消发布不被迟到索引/重建/普通编辑恢复。
- 每请求SQL快照过滤发布/版本/删除，真实Milvus dense＋SQL快照应用层BM25，RRF融合；Embedding失败关键词仍工作。分数类型、候选/重排/证据数量、调用来源与当前耗时明确；缓存按epoch/配置隔离，生成及校验前复核版本。不是原生FULLTEXT，也不把排名当回答正确概率。
- 前端显示草稿/索引/失败和当前服务版本，编辑携带打开时的expected_version。业务权限仍为已认证用户共享读发布知识、管理员写，没有另定文档ACL规则。

## 测试层次与原始证据

T0：scripts/test_offline.py，临时工作目录、合成SQLite、禁网络，不碰业务数据。基线知识相关35项通过（reports/offline/20261103-171446-448324）。全量中间轮547通过（172845-205528），再一轮550通过（173851-853750）；最终全量554 passed、0 failed/skip、371.79秒、退出码0（175446-899534）；随后只追加SQLite迁移/真实权限各1项并分别通过，没有将它们冒充同轮556。最后受影响82项通过（175349-071707），覆盖取消发布竞态、缺Milvus禁止发布、100chunk五批、真实Job消费/到期失败、RRF闸门和生成前版本失效。

C：tests/test_m3_embedding.py使用真实httpx请求编码/MockTransport供应商响应；100输入5批、乱序按index还原，缺项/重复index/布尔index/空/零/错维度拒绝且不污染缓存。此层不证明真实供应商可用。tests/test_m3_search.py覆盖Embedding超时、Milvus错误、Reranker池外结果、指纹/知识epoch缓存隔离及原文例外不误去重。

T1/F：scripts/setup_m3_test.py只创建label=aicshub.test.owner:m3-closeout资源；scripts/test_m3_real.py核对前缀/标签，清空Settings环境后禁真实模型，每例新MySQL合成库。Milvus2.5.10、pymilvus2.5.6、MySQL8.0及合成3维向量，不是模型语义质量。

- 首轮5项通过，reports/m3-closeout/real-20261103-172333，304.87秒。
- 迁移/实际双缓存读进程/索引退出补强5项通过，real-20261103-173005，57.13秒。
- 全套7项通过，real-20261103-173807，320.15秒，退出码0；双实际编辑进程20轮每轮仅一胜一409，服务实例读取新发布版；MySQL同名/失败/重试/重启与历史引用；实际os._exit(26)在索引前后，恢复同version不增加活动chunk；两个实际缓存读进程删除后都无旧内容。
- 发布事务与持有SQL快照的并发读补强2项通过，real-20261103-174523，41.73秒。读事务在epoch查询后暂停，另一事务切v2，原事务仍读完整v1，下个快照完整v2，没有混版。
- schema、index与逐路独有命中见reports/m3-closeout/milvus-schema-and-routes.json：关键词与向量路各一个独有候选，应用层BM25保留原词，不使用无法证实的旧FULLTEXT声明。

B：生产Vite构建、独立合成UI库，真实知识/认证/任务接口和独立worker，Embedding为明确标记的合成服务。前端7文件28项与TypeScript/Vite构建通过。实际浏览器：新增v1草稿→独立worker验证后发布；编辑同一document至v2→合成Embedding故障，页面显示失败且“当前服务v1”→管理员审计重试原Job→独立worker后显示“当前服务v2”。期间app真实进程57980→39400→39596重启，原文档/版本保留；没有浏览器网络mock。

截图/AX：reports/m3-closeout/browser/failed-keeps-v1.png、retry-published-v2.png及同名txt；最终控制台error=[]。没有使用真实用户资料，也未操作业务知识库。

L真实供应商：用户单独授权最多2请求/8条合成短文本/每条<=100字/关闭重试。scripts/test_m3_model.py用持久预算账本限制再次执行。实际2请求、5条合成输入，两次HTTP400；第二次明确AllocationQuota.FreeTierOnly：免费额度耗尽且仅免费模式阻断。第一次未保存错误正文，不推测第一次具体原因。两次均未取得真实向量，没有真实模型索引，聊天模型/Reranker调用为0。

原记录：reports/m3-closeout/model/first-failure.json、embedding.json，退出码均1。第二次显式请求配置dimensions后仍被账号额度拒绝，不能证明供应商维度参数兼容。未改付费开关、未充值、未追加调用；费用/usage未回传，不推测实际计费。恢复供应商可用额度并重新授权有限复验前，这两项保持未通过。

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
| 11-08 | C乱序/缺项/空/错维度与dimensions编码通过；L供应商HTTP400，无真实向量可核验 | BLOCKED（真实供应商） |
| 11-09 | T0同维度revision改变拒绝旧dense；T1指纹隔离collection已验证；L未取得真实供应商向量 | BLOCKED（真实供应商） |
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

唯一早期本地失败是临时文件测试错误纳入同fixture的knowledge.db；修正为独立uploads目录后通过，没有弱化“不得留临时文件”的断言。原日志reports/offline/20261103-172705-201832保留1 failed/22 passed。真实供应商失败记录如上，未skip/xfail掩盖。

未运行：生产业务库0009迁移/部署/备份恢复；真实收费模型成功路径、真实reranker、生产真实语料检索质量/长负载；原生Milvus稀疏BM25不是当前实现。当前SQL降级投影最多10000活动chunk，超限明确错误；大规模预算须另验。AC-12-05/08/10/12的模型效果/覆盖校准按原计划留M4/M5，没有降低本阶段冻结条目。

真实模型受外部额度阻塞，M3整体验收尚未完成；本轮代码/本地与依赖测试封存后停止，不自行切M4或改变供应商付费模式。原未跟踪三文件原样保留，不推送、不清空任何业务数据/知识/卷。提交/源码哈希/原始门禁和文件清单分别见Git历史、M3-GATES-v1.json与M3-FILES-v1.md。


提交：b11aea0范围；b2c87a2稳定版本/检索；467d400任务/API；40fd9e3前端；eaaa0e5取消发布/缺索引保护；8188b02维度/重排契约；075e01f真实依赖/模型预算/迁移权限验证。原始日志中的HEAD包含当时未提交工作区，不能把测试结果冒称历史HEAD干净构建；最终代码提交与源码哈希已另记于门禁。
