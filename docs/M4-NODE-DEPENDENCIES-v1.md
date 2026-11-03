# M4节点依赖清单 v1

2026-11-04。按当前源码AST核对图对象属性访问；本表不是所有模块/工厂/领域服务的完整依赖图，不能作为AC-07-08已全面完成的证明。事务与资格继续位于共享业务服务。

| 节点 | 主图实现 | 图对象属性访问 | 状态 |
| --- | --- | --- | --- |
| `start` | `start_node.py:start_node` | 无 | 无图属性依赖 |
| `intent_recognition` | `intent_node.py:recognize_request` | IntentDependencies(recognizer, model_factory, confirmed, cancelled, smalltalk, escalation, actions) | 已显式注入 |
| `route_decision` | `intent_node.py:route_request` | RoutingDependencies(extract_info, refund_inquiry, complaint_inquiry, missing_slots, destination) | 已显式注入 |
| `slot_filling` | `intent_node.py:fill_slots` | 无，仅读写本轮state | 已独立状态函数 |
| `clarification` | `intent_node.py:clarify_request` | ClarificationDependencies(recognizer) | 已显式注入 |
| `query_rewrite` | `rag_node.py:rewrite_query` | QueryRewriteDependencies(rewriter) | 已显式注入 |
| `rag_retrieval` | `rag_node.py:retrieve_evidence` | RetrievalDependencies(knowledge_factory, top_k, minimum_score) | 已显式注入 |
| `tool_execution` | `tool_node.py:execute_tools` | ToolExecutionDependencies(extract_info, select_tools, confirmed, invoke, actions) | 已显式注入 |
| `confirmation` | `tool_node.py:confirm_action` | ConfirmationDependencies(confirmed, pause) | 已显式注入 |
| `result_verification` | `tool_node.py:verify_tool_result` | 无，仅读取本轮ToolCall记录 | 已独立状态函数 |
| `response_generation` | `response_node.py:response_generation_node` | _add_rag_citations、_answer_cache_get、_answer_cache_set、_build_answer_cache_key、_generate_response_with_llm、_get_default_reply、_try_templated_tool_reply | 旧绑定待迁移 |
| `human_handoff` | `handoff_node.py:human_handoff_node` | 无 | 无图属性依赖 |
| `end` | `start_node.py:end_node` | 无 | 无图属性依赖 |

主图通过QueryRewriteNode/RetrievalNode callable对象注册，不使用对应旧self私有绑定。旧query_rewrite_node/rag_retrieval_node函数仅是内部兼容适配器，适配器内允许延迟查graph工厂，核心rewrite_query/retrieve_evidence不反向导入graph。知识工厂和同步search保持to_thread边界，异步search直接await。

新节点可声明自己的服务Protocol和冻结依赖dataclass后注册，不必把新助手加到AgentGraph类上。现有其他节点的旧助手绑定仍需逐个迁移，不以两节点完成代替整图重构。

工具执行/确认主图现注册ToolExecutionNode/ConfirmationNode，结果核验注册verify_tool_result。原私有函数和提取/确认/选工具助手仍是兼容适配器，主图使用无图公共策略函数；持久动作准备与有效pending计数经RefundActionCoordinator进入共享领域服务。授权签名/MCP路由/只读计划依赖的模块工厂仍须在完整模块依赖审查中列出，不能从无self访问推导无其他服务依赖。

意图识别/路由/澄清已注入服务与纯策略，槽位提示独立。model_factory仅在确实需要模型时解析，兼容实际调用入口替换模型服务，纯确认/取消/确定问候不访问模型；取消动作通过RefundActionCoordinator原身份/会话规则。其他模块工厂和响应组件仍需完成审查，不能以依赖表零self访问代替整个系统的依赖正确性。
