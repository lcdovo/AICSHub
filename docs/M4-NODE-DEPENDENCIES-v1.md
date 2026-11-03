# M4节点依赖清单 v1

2026-11-04，当前主图13节点装配与模块引用审查。历史迁移证据见M4-EVIDENCE-v1.md；本表描述当前候选代码，不代表已部署或M4整体通过。

| 节点 | 主图入口 | 明确服务/策略依赖 |
| --- | --- | --- |
| start | start_node.begin_turn | 仅AgentState |
| intent_recognition | IntentNode / recognize_request | 识别器、延迟模型工厂、确认/取消、问候/升级纯策略、动作取消领域接口 |
| route_decision | RoutingNode / route_request | 提取、退款/投诉咨询、补槽、目标映射纯策略 |
| slot_filling | intent_node.fill_slots | 仅AgentState |
| clarification | ClarificationNode | 识别器澄清接口 |
| query_rewrite | QueryRewriteNode | rewriter Protocol |
| rag_retrieval | RetrievalNode | knowledge_factory、top_k、minimum_score |
| tool_execution | ToolExecutionNode | 提取/选工具/确认纯策略、私有授权与MCP调用器、RefundActionCoordinator |
| confirmation | ConfirmationNode | 确认策略、受控interrupt暂停接口 |
| result_verification | tool_node.verify_tool_result | 本轮ToolCall记录 |
| response_generation | ResponseNode / ResponseContext | 模型、独立校验器、Judge、缓存、知识快照、用户记忆六个服务工厂；可替换生成器/工具模板仍受最终审查 |
| human_handoff | HandoffNode | 私有授权签名、统一MCP调用器；回执归属/状态核验 |
| end | start_node.finish_turn | 仅AgentState |

## 装配和模块边界

- Graph._register_nodes注册上述组件或独立函数，不注册绑定到Graph的业务节点方法。运行时注册表用例确认无node.__self__指向Graph，屏蔽旧私有节点后完整问候链仍运行。
- 响应助手归ResponseNode域内，不由Graph跨文件调用业务私有助手。ResponseContext是组件局部结构契约；Graph仍保留旧内部方法别名供已有直调兼容，它们不参与主图装配。新增节点只需声明依赖并注册，不必添加一组Graph私有助手。
- 图/节点依赖AgentState及请求/证据/审查契约；模型预算、资格、身份、精确确认、金额、幂等和事务在网关/领域服务中执行。工具节点内原pending SQL已归RefundActionCoordinator；主图编排器没有退款SQL事务。
- 规则/识别器不接收模型Principal；动作调用器签名依赖可信本轮source_message_id/actor，原领域服务再次核验。MCP回退仍是同一工具与业务服务，未另建REST/WS业务链。
- knowledge_factory与同步检索/快照检查运行在to_thread；异步服务直接await，未把阻塞SQL/模型调用移到事件循环。
- 实际模型工厂在需要调用时解析；当前确认/取消/确定问候不解析模型。正式Run身份、预算和策略通过同一上下文继承，独立入口有可信用户scope。
- 流式通道移至stream_context.py，由Graph和ResponseNode共享同一个ContextVar；Graph._stream_event_queue是兼容别名。响应模块不反向导入Graph；仅在最终校验通过后排队用户文本。
- 核心意图、路由、RAG、工具、响应、人工组件不依赖Graph类。rag_retrieval_node旧适配器保留延迟Graph工厂查找，只用于兼容内部直调，不是主图节点实现；不存在因此产生的主图反向导入。

## 验证范围

响应/人工/流式/证据/真实隔离SQL业务的117项相关T0检查通过，新增5项响应组件专项包含独立运行、双次审查、被拒草稿缓冲、注入快照失效、跨用户人工回执与主图旧绑定禁用。生成重试的工单/退款真实记录和工具执行次数断言未改；测试只把故障注入点从Graph私有方法迁到ResponseNode公开generate/tool_reply依赖。

这是AC-07-08的模块装配/路由契约证据。AC-07-01仍未完成：collected_info中还有字符串状态键，领域参数与观察元数据的全面类型契约不能由“显式服务依赖”替代；AC-06真实质量门槛失败也没有变化。

服务替换补核验：Graph主模型被替换后，原本共享旧主模型的LLMQueryRewriter/LLMRAGJudge同步到新实例；显式专用模型保持不变。这样有界测试请求hook可覆盖原共享解析链，不会因旧引用漏捕获；旧服务仍由拥有者在安全时机关闭，本补丁不是生产热切换协议。
