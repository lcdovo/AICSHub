# M2修改文件清单 v1

范围：基线789ae38至本轮M2完成。原未跟踪整体优化方案/验收规范与scripts/_dev_run_import.py未纳入提交。完整行为、OPT/AC映射、证据与未验证范围见M2-EVIDENCE-v1.md。

## 执行与存储

- [alembic/versions/0008_m2_runs_jobs.py](../alembic/versions/0008_m2_runs_jobs.py)
- [app/agent/graph.py](../app/agent/graph.py)
- [app/agent/memory.py](../app/agent/memory.py)
- [app/agent/nodes/response_node.py](../app/agent/nodes/response_node.py)
- [app/agent/state.py](../app/agent/state.py)
- [app/agent/tools.py](../app/agent/tools.py)
- [app/main.py](../app/main.py)
- [app/models/models.py](../app/models/models.py)
- [app/routers/chat.py](../app/routers/chat.py)
- [app/routers/jobs.py](../app/routers/jobs.py)
- [app/routers/sessions.py](../app/routers/sessions.py)
- [app/schemas/schemas.py](../app/schemas/schemas.py)
- [app/services/background_jobs.py](../app/services/background_jobs.py)
- [app/services/chat_service.py](../app/services/chat_service.py)
- [app/services/refund_actions.py](../app/services/refund_actions.py)
- [app/services/refund_application.py](../app/services/refund_application.py)
- [app/services/run_execution.py](../app/services/run_execution.py)
- [app/services/ticket_creation.py](../app/services/ticket_creation.py)
- [app/services/user_memory.py](../app/services/user_memory.py)

## 前端恢复

- [frontend/src/api/index.ts](../frontend/src/api/index.ts)
- [frontend/src/features/chat/protocol.ts](../frontend/src/features/chat/protocol.ts)
- [frontend/src/features/chat/transport.ts](../frontend/src/features/chat/transport.ts)
- [frontend/src/features/chat/useConversation.ts](../frontend/src/features/chat/useConversation.ts)
- [frontend/tests/transport.test.ts](../frontend/tests/transport.test.ts)

## 回归与真实依赖验收

- [acceptance/m2/real_services.py](../acceptance/m2/real_services.py)
- [acceptance/m2/server.py](../acceptance/m2/server.py)
- [scripts/run_worker.py](../scripts/run_worker.py)
- [scripts/test_m2_real.py](../scripts/test_m2_real.py)
- [tests/test_m2_context.py](../tests/test_m2_context.py)
- [tests/test_m2_jobs.py](../tests/test_m2_jobs.py)
- [tests/test_m2_runs.py](../tests/test_m2_runs.py)
- [tests/test_session_creation.py](../tests/test_session_creation.py)

## 协作与文档

- [AGENTS.md](../AGENTS.md)
- [docs/AGENT_GRAPH.md](AGENT_GRAPH.md)
- [docs/API.md](API.md)
- [docs/DEPLOYMENT.md](DEPLOYMENT.md)
- [docs/M2-CLOSEOUT-v1.md](M2-CLOSEOUT-v1.md)
- [docs/M2-EVIDENCE-v1.md](M2-EVIDENCE-v1.md)
- [docs/M2-FILES-v1.md](M2-FILES-v1.md)
- [docs/M2-GATES-v1.json](M2-GATES-v1.json)
- [docs/M2-IMPLEMENTATION-v1.md](M2-IMPLEMENTATION-v1.md)
- [docs/SECURITY.md](SECURITY.md)
