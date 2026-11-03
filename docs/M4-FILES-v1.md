# M4及本地模型切换修改文件

比较基线73614f5，含冻结范围提交d045398。仅列本轮主题文件。已有未跟踪方案/验收规范和scripts/_dev_run_import.py未纳入提交。

- `AGENTS.md`
- `acceptance/m4/README.md`
- `acceptance/m4/critical.jsonl`
- `acceptance/m4/dataset-manifest.json`
- `acceptance/m4/development.jsonl`
- `acceptance/m4/holdout.jsonl`
- `acceptance/m4/independent.jsonl`
- `acceptance/m4/real_services.py`
- `alembic/versions/0010_m4_model_gateway.py`
- `alembic/versions/0011_m4_model_revision.py`
- `app/agent/graph.py`
- `app/agent/nodes/intent_node.py`
- `app/agent/nodes/rag_node.py`
- `app/agent/nodes/response_node.py`
- `app/agent/read_plan.py`
- `app/agent/request_understanding.py`
- `app/agent/state.py`
- `app/config/config.py`
- `app/main.py`
- `app/models/models.py`
- `app/routers/model_calls.py`
- `app/services/chat_service.py`
- `app/services/embedding_service.py`
- `app/services/llm_service.py`
- `app/services/model_gateway.py`
- `app/services/run_execution.py`
- `docker-compose.yml`
- `docs/AGENT_GRAPH.md`
- `docs/API.md`
- `docs/DEPLOYMENT.md`
- `docs/LOCAL-MODEL-SWITCH-v1.md`
- `docs/M4-CLOSEOUT-v1.md`
- `docs/M4-EVIDENCE-MANIFEST-v1.json`
- `docs/M4-EVIDENCE-v1.md`
- `docs/M4-FILES-v1.md`
- `docs/M4-GATES-v1.json`
- `docs/M4-IMPLEMENTATION-v1.md`
- `docs/SECURITY.md`
- `docs/llm-governance.md`
- `scripts/evaluate_m4_live.py`
- `scripts/reindex_local_models.py`
- `scripts/test_m4_real.py`
- `tests/test_local_model_switch.py`
- `tests/test_m4_api.py`
- `tests/test_m4_evaluation.py`
- `tests/test_m4_gateway.py`
- `tests/test_m4_understanding.py`

后续切片新增修改：`app/services/llm_governance.py`、`tests/test_m4_privacy.py`；实体来源切片沿用request_understanding.py及其测试。

预算故障切片新增：`tests/test_m4_fault_matrix.py`；修改model_gateway.py、llm_service.py及相关验收/接口文档。

策略切片新增：`tests/test_m4_policy.py`，影子通知常量及意图/响应节点；完整图审查失败不得使影子写入人工请求。

主图只读计划切片新增：`tests/test_m4_read_plan.py`；修改read_plan.py、request_understanding.py、state.py及tool/response节点。

阶段边界验收切片新增：`tests/test_m4_stage_boundaries.py`，无生产代码变更。

动作恢复切片新增：`tests/test_m4_action_projection.py`；修改ChatService统一hydrate，SQL动作覆盖旧投影/缓存确认。

迟到结果切片新增：`tests/test_m4_late_results.py`；修改model_gateway.py、llm_service.py、run_execution.py。

用户归属切片新增：`tests/test_m4_model_identity.py`；修改model_gateway.py、llm_service.py、graph.py、memory.py、knowledge路由及现行说明。

类型化证据切片新增：`tests/test_m4_evidence_contract.py`；修改state.py、request_understanding.py、RAG/response节点。

订单路由/否定范围切片新增：`tests/test_m4_entity_routing.py`；修改request_understanding.py与intent/tool节点的旧提取兼容边界。

连接生命周期切片新增：`acceptance/m4/local_gateway_soak.py`、`tests/test_m4_shutdown.py`；修改llm_service.py、main.py及网关/故障测试和真实依赖执行入口。

RAG节点依赖切片新增：`tests/test_m4_node_dependencies.py`、`docs/M4-NODE-DEPENDENCIES-v1.md`；修改graph.py、rag_node.py及对应验收/架构说明。

工具依赖切片新增：`tests/test_m4_tool_dependencies.py`；修改graph.py、tool_node.py、refund_actions.py及依赖清单/验收说明。

意图依赖切片新增：`tests/test_m4_intent_dependencies.py`；修改graph.py、intent_node.py、退款领域取消门面及依赖/验收说明。

响应装配切片新增：`app/agent/stream_context.py`、`tests/test_m4_response_dependencies.py`；修改响应/人工/开始结束节点、Graph装配、原阶段测试的注入位置和模块依赖文档。

工具观察切片新增：`app/agent/tool_contracts.py`、`tests/test_m4_tool_observations.py`、`docs/M4-STATE-CONTRACT-v1.md`；修改AgentState与工具/人工观察入口。

流程结论切片新增：`app/agent/flow_contracts.py`、`tests/test_m4_flow_contract.py`；修改AgentState、统一理解及意图/检索/响应/人工节点。

测试来源切片新增：`scripts/test_provenance.py`、`tests/test_test_provenance.py`、`docs/TEST-EVIDENCE-v1.md`；修改T0执行器使源码漂移的绿色用例不被误当有效证据。

全项目需求审计新增：`docs/PROJECT-AC-INDEX-v1.md`、`docs/PROJECT-AC-INDEX-v1.json`，覆盖原195项AC并保留标准哈希与分层证据限度。

类型状态夹具补验：`tests/test_m4_response_dependencies.py`改用正式set_flow并断言实际缓存读取，生产代码无新变更；全量751及强化后69项分别封存。

生成元数据切片新增：`app/agent/generation_contracts.py`、`tests/test_m4_generation_metadata.py`；修改AgentState、响应节点、ChatService与LLMService观察计量。

SQL轮次恢复切片新增：`tests/test_m4_sql_turn_reset.py`；修改AgentState跨轮白名单与ChatService SQL hydrate复用，数据不删除。

业务参数切片新增：`app/agent/business_contracts.py`、`tests/test_m4_business_parameters.py`；修改AgentState及意图/工具/RAG/响应、统一理解与SQL恢复的参数入口。

模型重绑定切片新增：`tests/test_m4_model_rebinding.py`；修改Graph.llm_service属性以同步其原共享解析组件，保留显式独立Judge。

确认绑定切片新增：`app/agent/confirmation_contracts.py`、`tests/test_m4_confirmation_binding.py`；修改AgentState与工具/确认节点，旧bool不作为授权来源。
