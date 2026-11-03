# M1固定收尾修改文件

范围：27d868c之后本次收尾提交。原未跟踪两份v1方案/规范与开发导入脚本保持未跟踪，未纳入提交。

- `.env.example`
- `acceptance/m1/real_services.py`
- `acceptance/m1/server.py`
- `alembic/versions/0007_m1_refund_actions.py`
- `app/agent/intent.py`
- `app/agent/mcp_client.py`
- `app/agent/mcp_server.py`
- `app/agent/nodes/intent_node.py`
- `app/agent/nodes/response_node.py`
- `app/agent/nodes/tool_node.py`
- `app/agent/query_rewrite.py`
- `app/agent/query_rewrite_llm.py`
- `app/agent/read_authorization.py`
- `app/agent/receipt_recovery.py`
- `app/agent/refund_authorization.py`
- `app/agent/state.py`
- `app/agent/tools.py`
- `app/config/config.py`
- `app/main.py`
- `app/models/models.py`
- `app/routers/chat.py`
- `app/routers/refunds.py`
- `app/schemas/schemas.py`
- `app/services/chat_service.py`
- `app/services/llm_governance.py`
- `app/services/refund_actions.py`
- `app/services/refund_application.py`
- `app/utils/tracking.py`
- `docs/AGENT_GRAPH.md`
- `docs/API.md`
- `docs/DEPLOYMENT.md`
- `docs/M1-CHANGES-v1.md`
- `docs/M1-CLOSEOUT-v1.md`
- `docs/M1-EVIDENCE-v1.md`
- `docs/M1-R1-R2.md`
- `docs/SECURITY.md`
- `frontend/src/features/chat/RefundPanel.tsx`
- `frontend/src/features/chat/transport.ts`
- `frontend/src/features/chat/useConversation.ts`
- `frontend/src/pages/ChatPage.tsx`
- `frontend/tests/refunds.test.tsx`
- `scripts/test_m1_real.py`
- `tests/test_chat_turns.py`
- `tests/test_m1_privacy.py`
- `tests/test_m1_read_tools.py`
- `tests/test_m1_refund_actions.py`
- `tests/test_m1_tool_contracts.py`
- `tests/test_refund_persistence.py`

- `docs/M1-GATES-v1.json`
- `docs/M1-MODEL-EVIDENCE-v1.json`

测试原始日志、截图、数据库与构建产物仅在忽略的reports内，不提交。
