# M3知识生命周期实施契约 v1

OPT-11/12正确性，固定21项见M3-CLOSEOUT-v1.md。

## 文档事实与迁移

knowledge_docs保留原数字主键，新增stable_id、published_version与生命周期字段；knowledge_versions保存独立不可变正文/元数据版本与索引状态/指纹；knowledge_chunks保留版本、稳定chunkID、全文hash、原文范围、章节、FAQ问题/表头上下文及策略。编辑通过expected_version条件写入CAS；同名文档独立处理。删除/取消发布只改变SQL活动指针与epoch，保留历史原文、版本、chunk与旧collection。

0009仅添加字段/表并建立legacy数字别名doc_db_N/doc_mysql_N→稳定文档。旧随机doc_UUID未持久，不能可靠重建，不能按标题合并；历史chunk ID仍保留。旧发布正文保留SQL关键词降级，旧向量因空间指纹未知不参与dense；显式重建新版本才使用新指纹空间。迁移不自动调用模型、不清空或重建业务索引，不自动有损downgrade。本轮只演练隔离合成库。

## 任务与发布

保存返回draft/version_id/job_id，业务版本与knowledge_index Job同事务入库；这不是索引完成。独立命令python scripts/run_worker.py --kind knowledge_index消费（生产启动另行授权）。解析支持有界txt/md/json/csv，读取64KiB块先限大小；文本解析在线程中10秒超时；重分块/向量化/索引在独立worker。批量逐文件报告失败与排队，不把draft称已发布。

索引按版本生成确定性chunkID，batch按EMBEDDING_BATCH_SIZE切分；HTTP返回严格按index映射、核对数目、维度、有限非零值，拒绝补零/截断。每批/外部写前后检查Job租约，阶段租约120秒，底层API有超时与有限重试。失败版本failed，原发布指针不变；最后一次尝试进程消失也收敛failed。重试同version幂等upsert，不新增版本。显式重建才创建新版本。

指纹包含无密钥provider/model/revision/dim、查询指令、归一化及配置标识。新collection命名为原前缀_m3_指纹，不连接旧collection补索引；核对维度、写入chunk集合和指纹后，SQL事务检查最新文档版本与未删除状态才切换published_version/epoch。迟到旧版本可ready但不覆盖较新编辑者的发布；外部孤儿向量只属不可见旧version，清理不在本轮执行。

## 检索与缓存

每请求从SQL同一事务读取epoch和发布版本快照；当前知识库仍为所有已认证用户共享读取、管理员写，没有新设文档ACL业务规则。SQL过滤草稿/已删除/非发布版先于关键词、向量与缓存。Milvus按精确允许chunk ID与指纹过滤，SQL再次映射只使用可信原文。

当前采用真实Milvus dense + SQL快照应用层BM25 + 应用层RRF，明确不声称原生FULLTEXT。两路独立，Embedding失败/超时不短路BM25；Milvus不可用以同指纹SQL向量降级。provider调用全进程最多4个线程槽，调用方5秒等待预算，超时工作仍占槽直到底层超时结束，避免无限后台积压。SQL小规模投影最多10000活动chunk，超限明确错误；大规模服务端稀疏检索/容量规划另行验收。

保留dense_score/bm25_score/rrf_score/rerank_score/score_type，RRF是排序值，不与余弦0.35或概率混比。recall_k、rerank_k、evidence_k独立；重排失败/无配置保持原排名，拒绝池外/重复/数量不符返回；不使用前100字去重。每结果保留全文有界chunk、原文位置和来源上下文，生成/Judge不再裁剪300/200字前缀。支持程度仍unknown，M5负责回答覆盖校准，M3不以高排名宣称答案事实正确。

缓存键含epoch、精确查询、指纹、reranker配置、数量/阈值及既有权限范围。每次仍核对SQL epoch，传播窗口为下一检索SQL快照；无需扫描删除所有回答缓存。返回本次耗时、冷/热来源、逐路ID与降级；冷热结果复制，不污染缓存。生成前和响应校验前复核发布版本，发生改变则拒绝使用旧证据。

## 验证边界

合成Embedding验证协议/错配/批次，不证明真实模型语义效果；真实MySQL/Milvus/实际进程证据单列。本地Ollama Embedding与Infinity Reranker真实集成及应用检索重启已验证；真实外部收费Embedding探测两请求曾遇免费额度上限（见证据），没有再次调用。真实聊天LLM/外部reranker、业务库升级、生产部署未运行。最终逐项结果见M3-EVIDENCE-v1.md。


本地真实模型复验：宿主机Ollama地址http://127.0.0.1:8012/v1、模型qwen3-embedding:0.6b、实际1024维；Infinity地址http://127.0.0.1:7997、模型BAAI/bge-reranker-large。仅专属合成验收进程显式使用，项目.env未切换。脚本python scripts/test_m3_local_models.py核对真实测试Milvus的label/端口，写新collection后验证查询与revision空间隔离；仍不代表业务旧索引已重建或生产部署完成。
