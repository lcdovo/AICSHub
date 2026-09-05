# RAG 可靠性与可复现评测

第 5 步为知识库增加了可恢复的文档/chunk 存储和明确的生命周期：

- `knowledge_docs` 保存正文、关键词、来源、发布状态、版本号和软删除时间。
- `knowledge_chunks` 保存 chunk 正文、稳定 `chunk_id`、embedding、chunk 版本和启用状态。SQLite 本地 profile 与 MySQL 集成 profile 使用同一契约。
- 应用启动时恢复已发布文档和 chunk；`POST /api/v1/knowledge/rebuild-index` 可从持久化内容重建 BM25 索引。
- `PATCH /api/v1/knowledge/documents/{id}` 生成新版本并停用旧 chunk；删除使用软删除，旧内容不会继续召回。发布状态可通过 `POST /api/v1/knowledge/documents/{id}/publish?published=false` 切换。
- Milvus/Embedding 未配置时使用内存 + BM25/Mock embedding，并在搜索结果和统计中返回 `backend`、`degraded` 与 `degraded_reason`，不会静默假装高可用。

## 评测

使用固定数据集运行本地 benchmark：

```powershell
python scripts/rag_benchmark.py
python scripts/rag_benchmark.py --dataset path/to/dataset.json --top-k 5
```

数据集是 JSON 数组，每项包含 `query` 与 `relevant_titles`，未知问题可将其设置为空数组。脚本输出 Recall@K、MRR、Precision@K、空召回率和延迟 P50/P95，同时标记检索后端和降级原因。默认数据集覆盖退款、订单/物流、人工客服和未知问题；不依赖真实 LLM、Embedding 或 Milvus，结果可在本地重复运行。

数据库结构升级使用 Alembic：

```powershell
alembic upgrade head
```

