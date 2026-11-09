# RAG结构与检索优化 v2

日期：2026-11-07。范围是入库分块、召回、融合、重排输入、证据组装和检索缓存；没有修改意图识别、问题理解、回答审查或业务动作流程。基于已有未提交工作实现，本文件不是部署或M5整体验收记录。

## 入库路径

SQL不可变版本 → 持久knowledge_index Job → 共用结构分块 → 上下文检索文本 → 批量Embedding → 新指纹collection验证 → SQL发布指针/epoch。

- `chunking.source_chunks()`是唯一原文分块实现。`DocumentChunker`兼容入口转用同一实现，生产索引Job显式传入CHUNK配置。
- Markdown ATX `#`至`######`标题和FAQ问题是硬边界，继承完整标题路径；代码围栏中的伪标题不解析。标题前正文保留，只有标题的父节并入首个子节。
- 同一结构单元超过`CHUNK_SIZE`才细分。heading/paragraph优先段落、整行，再句末；sentence优先句末；fixed按窗口。超长单行仍硬切，表头由上下文补充。尚不是完整Markdown AST解析器，也没有一般语义识别步骤依赖的保证。
- `CHUNK_SIZE`现为原文字符硬上限（64～8000），不再使用旧实现的800字符触发阈值。`CHUNK_OVERLAP`仅发生在同一单元内，0有效，必须小于size。
- 每块`content`严格等于原文`[start_offset:end_offset]`，保留hash/位置/独立身份；标题、FAQ问题、表头保存在source_context中。`section`限制200字符，完整路径在JSON上下文。
- 每个版本第一次索引时，将size/overlap/pattern及策略版本冻结到`embedding_metadata.index_plan`。重试沿用，不能因修改进程配置改变同一版本的chunk身份。

## 一致的检索文本

`retrieval_text()`为文档向量、BM25和Reranker共用入口，添加文档标题、章节、所属问题、表头和关键词。已存在于正文的完整字段不重复添加。上下文前缀最多1000字符，原文尾部不截断。

文本策略`context-document-v2`进入Embedding指纹，不能让旧的正文向量冒充新文本索引。chunk策略是`structured-source-v2`。

Reranker保留原候选限制和截止时间；超过2000字符的输入整体保留RRF排名并记录降级，不静默截掉尾部例外。外部供应商候选仍一次请求批量打分，本地BGE维持既有batch4/并行2，没有为新组装模块增加模型请求。取得完整候选排序后再选最终证据。

## 召回与缓存

- BM25使用对数IDF、预计算词频/长度和倒排表；查询只分词一次，按匹配posting评分。中文双字词仅在连续汉字串内产生，型号保留完整连字符标识及组成词。
- 每次请求轻量读取SQL发布epoch，匹配时复用进程内快照；变化时读取同事务epoch与正文的新快照。每个worker独立检查SQL，不用TTL代替取消发布检查。
- 同一快照懒构建并复用BM25索引，dense-only路径不构建。旧请求不能覆盖新epoch的索引。
- 快照和词法构建加锁，查询结果深复制，避免并发重复构建及调用方污染缓存。QueryCache同步读写加锁，TTL使用单调时钟。
- 主检索结果缓存仍为进程内缓存，未声称已接通Redis共享结果缓存。相同查询的模型推理single-flight、多进程共享索引和Milvus原生BM25没有在本轮引入。
- 缓存key包含epoch、Embedding指纹、BM25/文本/组装策略版本以及RRF与证据预算参数。
- 保留Milvus失败时SQL兼容向量回退、Embedding失败时词法回退、重排失败时RRF排名回退；排名不代表事实支持概率。

## 证据组装

按排序选择原始chunk，在`RAG_TOP_K`数量与字符预算内，补取同文档、同版本、同结构单元的有限邻块；优先下文，再上文。每个邻块仍保留自己的ID、原文和偏移，不拼造一个沿用旧ID的“大chunk”。

仅去掉同来源和相同上下文的完全重复正文，不按公共前缀或相似度删除否定/例外差异。不同文档来源不互相去重。超预算片段整块跳过，不截断；证据不足仍交给既有流程处理。

字符预算按`evidence_text`计算，不等于序列化JSON或整个LLM请求的token上限。长条件链超出邻块数量/预算时仍可能覆盖不全，不以本次组装替代回答校验。

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| RAG_RRF_K | 60 | 融合平滑常数 |
| RAG_EVIDENCE_MAX_CHARS | 8000 | 所选证据文本总字符预算 |
| RAG_CONTEXT_NEIGHBORS | 1 | 每个命中最多补取邻块数，0关闭，最大2 |
| RAG_RECALL_K / RAG_RERANK_K | 20 / 20 | 延续默认候选量；当前重排服务最多支持24候选 |

未修改本机`.env`或主模型、向量模型、维度；召回量、余弦阈值和GPU并行参数不凭小样本调优。

## 现有索引如何升级

本轮没有操作业务数据库、启动worker、重索引或删除collection/卷，不需要新增数据库迁移。新文本指纹与旧索引不同：部署新代码后，旧发布文档可通过SQL词法路径读取，dense会报告指纹不匹配，直到新版本索引完成。

正式切换需安排完整读端/worker升级窗口，禁止混用新旧写入路径。复用现有`reindex_local_models.py`的“新版本暂存、所有索引ready后原子切发布指针”流程，事先核对备份、运行配置及模型调用授权；该脚本会读写实际数据库并调用Embedding，本轮没有执行。失败保留旧发布指针，取消发布期间不能自动恢复。

## 离线证据与限制

新增`tests/test_rag_structure.py`覆盖层级、前言、围栏代码、长FAQ、表头、完整原文位置、配置生效、重试冻结、输入一致性、旧空间隔离、索引复用/并发、邻块引用、预算、重复片段及缓存参数变更。

`python scripts/evaluate_rag_structure.py --output <新的报告路径>`仅创建临时SQLite与合成向量，禁止查询Embedding，不调用真实模型。6份合成文档、8题，走生产Job/分块/检索；同时记录冷/热结果、源码指纹与执行期间是否稳定。示例报告在忽略目录`reports/rag-structure/`。它测量目标文档和指定原文是否进入Top3，不测生成论断支持率、真实Embedding/Reranker质量或GPU容量。

在原`adjudicated-v2`材料的55份完整合成文档、351条可回答题上，对比本轮修改前Git HEAD BM25与新BM25：

| 指标 | 修改前 | 修改后 |
| --- | --- | --- |
| Recall@20 | 1.0000 | 1.0000 |
| MRR | 0.984330 | 0.988061 |
| nDCG@5 | 0.988366 | 0.990096 |

该比较保持完整文档正文输入，只隔离BM25公式/分词变化，不是生产分块后的召回率。材料是assistant_reviewed合成资料，不是新增独立人工金标；不改变M4/M5验收状态。

回归执行需覆盖本机模型配置：测试进程禁用真实LLM/本地模型地址，REQUEST_UNDERSTANDING_MODE设rules；HTTP替身/ASGI允许，真实HTTP传输阻断。初次直接读取本机配置的扩大回归存在环境/接口失败，不能作为通过结果；收紧隔离后的结果另行记录。

### 本轮执行结果

- 新增RAG专项32项全部通过；最后一次变更后的RAG专项、Embedding及重排相关切片共63项通过。
- 扩展18份测试模块（含聊天轮次、REST/WS知识接口、配置及provider契约）在上述隔离下为205通过、1失败。唯一失败为`tests/test_m3_lifecycle.py::test_sqlite_legacy_migration_preserves_text_and_legacy_chunk`：旧版知识表专用测试库没有`refund_actions`，当前工作区的0012_agent_tasks迁移检查该表时抛NoSuchTableError。本轮未修改迁移/业务模型，不将扩展回归标为全绿。
- `python -m compileall -q app scripts`、受影响模块Ruff F401/F821和`git diff --check`通过。保留现有依赖及datetime弃用警告，未批量修复。
- `reports/rag-structure/20261107-v3.json`：源码指纹起止稳定，8/8目标文档和所需原文进入Top3；16次冷/热查询仅构建1次快照与1次词法索引。该小语料本机miss/hit的P95分别8.862/1.008ms，不能外推线上延迟或宣称同比加速。
- BM25比较报告`reports/rag-structure/bm25-before-after-20261107.json`记录Git基线5a52479、源码与材料SHA256。上表351题指标是完整文档组件对照，不是新链路整体验收。
- 诊断报告均在忽略目录，不把数据库、日志或构建产物加入版本控制。没有执行业务重索引、部署或真实模型验收。
