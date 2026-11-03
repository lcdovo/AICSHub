# 本机业务本地模型切换 v1

2026-11-04。**业务配置及发布索引已切换，本地实际推理链路已验证；默认候选规模的重排性能未通过。** 聊天模型仍为UINO，知识Embedding/Reranker独立使用本地模型。

切换前业务栈停止，卷为ai-customer_mysql_data/redis_data/etcd_data/minio_data/milvus_data。冷备份五卷、.env与敏感Compose配置、SQL逻辑备份及SHA256清单位于被Git忽略的reports/local-model-switch/20261103-233645，current.json可定位。备份含敏感业务数据，不入库不展示。没有执行清库、clear、down -v、种子导入或删除旧索引。

实际配置：Embedding `http://embedding-local:11434/v1`、`qwen3-embedding:0.6b`、1024维、revision `ollama-ac6da0dfba84`、batch2、HTTP导入时限90秒；Reranker `http://reranker:7997`、`BAAI/bge-reranker-large`。Ollama/Infinity复用原权重卷；Infinity CPU线程2/batch4，禁用torch compile及全尺寸启动基准，健康后另作实际推理核验。宿主机端口仍8012/7997，业务API8100。

迁移前0004，迁移后0011（含M2/M3事实表与M4账本）。scripts/reindex_local_models.py复制现有已发布内容到新版本、publish=False，逐篇新指纹索引ready后，所有文档在同一SQL事务校验版本和原发布指针并切换。初次batch20超时，无发布指针切换；batch2重跑通过。24文档、275活动chunk，新指纹72457796ac9a163f444568250a6636a1b5a48ba14c1e02be20cc0466dd62449a。

| 数据 | 切换前 | 核验后 |
| --- | --- | --- |
| users | 3 | 3 |
| orders | 22 | 22 |
| sessions | 64 | 64 |
| messages | 165 | 165 |
| tickets | 0 | 0 |
| knowledge_docs | 24 | 24 |
| knowledge_chunks总数 | 364 | 639（新增275，旧364保留） |
| knowledge_versions | 原迁移前无版本表 | 49（含首次失败草稿，保留） |

真实业务只做健康/文档接口、SQL计数和只读知识检索，未创建合成用户/订单/聊天。/health为healthy、数据库/Redis/Milvus健康，/docs 200。健康检查中LLM仅configured，不冒充真实模型调用。

实际检索记录在business-verification.log、business-warm-verification.log、business-tuned-verification.log、business-final-verification.log、business-minimal-verification.log。初次冷查询Embedding/重排超时；预热后Dense确实走Milvus。Infinity资源调整后单候选实际知识检索：Embedding583ms、dense351ms、rerank2345ms、reranker=local、degraded=false；短合成重排契约431ms。3候选仍5秒外层超时，真实默认20候选/约10候选P95性能未验收，不能用单候选成功代替。保持原5秒边界及降级metadata，未改验收2秒目标。

回滚应先停止知识写入/索引worker，核对当前CAS与备份记录，在事务中恢复明确的旧published_version并恢复匹配Embedding身份；绝不通过删除新/旧索引实现回滚。旧0004向量空间未知，恢复旧配置不代表旧向量可安全混用，应保持BM25降级并重新核验。数据库升级本身不自动降级。完整灾难恢复尚未演练，不把备份存在当恢复成功。

新增3个隔离SQLite回归：第二篇失败整体回滚、重建中取消发布不复活、全部ready同批切换且旧版本可读取，全部通过。它们验证事务语义，不替代Milvus/模型实测。
