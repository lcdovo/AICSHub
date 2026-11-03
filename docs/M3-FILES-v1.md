# M3修改文件清单 v1

范围：基线a063d13至M3本轮封存；不包含原未跟踪两份方案/规范与开发脚本。行为与AC证据见M3-EVIDENCE-v1.md。

## 知识版本、索引与检索

- [alembic/versions/0009_m3_knowledge_versions.py](../alembic/versions/0009_m3_knowledge_versions.py)
- [app/agent/nodes/rag_node.py](../app/agent/nodes/rag_node.py)
- [app/agent/nodes/response_node.py](../app/agent/nodes/response_node.py)
- [app/agent/validation_llm.py](../app/agent/validation_llm.py)
- [app/config/config.py](../app/config/config.py)
- [app/models/models.py](../app/models/models.py)
- [app/repositories/knowledge_repository.py](../app/repositories/knowledge_repository.py)
- [app/routers/knowledge.py](../app/routers/knowledge.py)
- [app/services/background_jobs.py](../app/services/background_jobs.py)
- [app/services/embedding_service.py](../app/services/embedding_service.py)
- [app/services/knowledge_base/__init__.py](../app/services/knowledge_base/__init__.py)
- [app/services/knowledge_base/index_admin.py](../app/services/knowledge_base/index_admin.py)
- [app/services/knowledge_base/index_worker.py](../app/services/knowledge_base/index_worker.py)
- [app/services/knowledge_base/ingestion.py](../app/services/knowledge_base/ingestion.py)
- [app/services/knowledge_base/lifecycle.py](../app/services/knowledge_base/lifecycle.py)
- [app/services/knowledge_base/retrieval.py](../app/services/knowledge_base/retrieval.py)
- [app/services/knowledge_base/vector_index.py](../app/services/knowledge_base/vector_index.py)
- [app/services/knowledge_base/versioned_search.py](../app/services/knowledge_base/versioned_search.py)
- [app/services/reranker_service.py](../app/services/reranker_service.py)

## 前端状态与编辑

- [frontend/src/api/index.ts](../frontend/src/api/index.ts)
- [frontend/src/api/types.ts](../frontend/src/api/types.ts)
- [frontend/src/components/ui.tsx](../frontend/src/components/ui.tsx)
- [frontend/src/features/knowledge/DocumentEditor.tsx](../frontend/src/features/knowledge/DocumentEditor.tsx)
- [frontend/src/pages/KnowledgePage.tsx](../frontend/src/pages/KnowledgePage.tsx)
- [frontend/tests/knowledge.test.tsx](../frontend/tests/knowledge.test.tsx)

## 隔离验证与脚本

- [acceptance/m3/real_services.py](../acceptance/m3/real_services.py)
- [acceptance/m3/server.py](../acceptance/m3/server.py)
- [acceptance/m3/work_once.py](../acceptance/m3/work_once.py)
- [scripts/import_rag_documents.py](../scripts/import_rag_documents.py)
- [scripts/run_worker.py](../scripts/run_worker.py)
- [scripts/setup_m3_test.py](../scripts/setup_m3_test.py)
- [scripts/test_m3_model.py](../scripts/test_m3_model.py)
- [scripts/test_m3_real.py](../scripts/test_m3_real.py)
- [tests/test_m3_api.py](../tests/test_m3_api.py)
- [tests/test_m3_embedding.py](../tests/test_m3_embedding.py)
- [tests/test_m3_lifecycle.py](../tests/test_m3_lifecycle.py)
- [tests/test_m3_search.py](../tests/test_m3_search.py)

## 协作与文档

- [AGENTS.md](../AGENTS.md)
- [docs/AGENT_GRAPH.md](AGENT_GRAPH.md)
- [docs/API.md](API.md)
- [docs/DEPLOYMENT.md](DEPLOYMENT.md)
- [docs/M3-CLOSEOUT-v1.md](M3-CLOSEOUT-v1.md)
- [docs/M3-EVIDENCE-v1.md](M3-EVIDENCE-v1.md)
- [docs/M3-FILES-v1.md](M3-FILES-v1.md)
- [docs/M3-GATES-v1.json](M3-GATES-v1.json)
- [docs/M3-IMPLEMENTATION-v1.md](M3-IMPLEMENTATION-v1.md)
- [docs/SECURITY.md](SECURITY.md)
