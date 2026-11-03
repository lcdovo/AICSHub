# M4状态契约清点 v1

2026-11-04。用于AC-07-01渐进迁移，当前仍PARTIAL。本表区分主事实、派生观察和旧兼容字段，不把“声明了类型名”当作全图类型化完成。

| 分区 | 当前权威字段/类型 | 旧兼容键或来源 | 生命周期/限制 |
| --- | --- | --- | --- |
| 身份/执行 | AgentState.user_id/actor_id/session_id/source_message_id/session_version、请求action_id/version | 可信HTTP/WS及SQL Run；collected_info中的user_id/session_id仅投影 | 工具数据/模型不能覆盖可信身份；每轮由服务恢复 |
| 对话背景 | ConversationContext、messages、summary | order_id、ticket_id、tracking_number、intent_history | 仅必要背景跨轮；历史不能确认新动作 |
| 请求与意向 | RequestUnderstanding、ActionIntent、read_plan | intent_source、shadow_understanding、smalltalk | 每轮重置；ActionIntent不是授权凭据 |
| 持久退款动作 | SQL RefundAction事实；RefundPreview/RefundActionView/TurnRefundConfirmation类型观察 | refund_preview/refund_action仅兼容镜像，reason/refund_type由BusinessParameters管理 | 视图不授权，SQL资格/本轮确认/幂等独立；异常和镜像变化停止确认 |
| 证据 | EvidenceBundle、retrieval_metadata、retrieval_error | kb_results/kb_search_meta/rag_grounded/best_match_score等投影 | 同一快照内容/版本/epoch核验；支持度由独立审查决定 |
| 回答审查 | ReviewResult(passed, evidence_version) | validation、answer_validated、llm_judge、inline_self_check | 本轮结果；独立校验和证据指纹绑定，不收工具自报的通过 |
| 工具观察 | ToolObservation与tool_observations[BusinessToolName] | 明确枚举的xxx_result | 本轮严格外壳、完整回执嵌套保留；data为不透明业务数据，不是权限或控制字段 |
| 工具审计 | ToolCall列表 | 原统一工具执行链与回执 | 参数/结果深复制，完整历史不被最新观察覆盖；不是当前确认来源 |
| 生成计量 | GenerationMetadata / ProviderTokenUsage严格类型观察头，SQL ModelCallRecord是费用事实 | llm_governance仅允许字段的兼容投影 | 不保存structured_output、输入摘要或额外凭据；partial/unknown usage不补0算实际费用，下一轮清空 |
| 发布/流程标记 | TurnFlow严格冻结模型+FlowFlag枚举，6项bool/None结论 | smalltalk/rag_grounded/rag_refused/answer_validated/answer_from_cache/handoff_attempted仅兼容投影 | 节点通过flow_value/set_flow读写；真实bool旧字段仅首次导入，已有/明确清空结论不被字典改写，不跨轮保留 |

## 工具观察入口

record_tool_observation只接受注册的8种业务工具名字，工具身份取调用器给出的tool_name而非回执自报。search_kb是实际注册工具，hybrid_search是RAG观察标签，后者由独立EvidenceBundle入口处理，二者不混用。

完整result.data只保留在ToolObservation.data、深复制的ToolCall和枚举xxx_result兼容投影中。仅允许投影order_id/ticket_id/refund_id/request_id/notification_id等对应工具的业务编号；已有请求选定订单不能被回执改成另一订单。user_id/session_id、confirm_refund/refund_preview、校验/证据/模型计量和人工流程标记均不从数据展开。

最新失败观察清除该工具旧成功xxx_result投影；历史ToolCall仍保留。供应商对象、兼容字段和类型观察之间不共享可变字典。for_next_turn清空工具观察、工具调用、当前证据/审查，持久化业务回执仍以SQL为准。

## 尚未完成

业务参数、预览、动作展示、生成计量和流程结论已迁移，仍需收口其他业务编号、意图历史、来源标签、改写/观察与兼容键的全面声明，并审查全部主链读写。保留兼容字典不能当作最终完成；工具外壳有类型也不代表业务data内容可信。权限/资格/幂等/确认仍必须由领域服务检查，模型最终输出仍必须走响应审查。

流程类型迁移：TurnFlow以None代表未执行/无结论，与False明确区分。set_flow完整验证strict bool与额外键后原子替换不可变模型，不使用绕过验证的model_copy(update=...)。旧Graph直调无结论时可一次性导入真实bool，字符串/数值不是结论；正式Run的clean next-turn及SQL versioned session snapshot不携带旧流程状态。intent_source/reply_source、意图历史和改写/观察等兼容键仍待进一步收口。

生成观察头迁移：模型/版本/降级/预算/tokens/耗时/有限价格字段明确声明并严格验证，计数非负、费用有限且非负、usage总数须自洽。usage_basis由计数字段完整性推导，不能用自报标签伪造真实usage。无效元数据转unknown+degraded禁止缓存，不代替回答审查；只对允许的标识字符串脱敏，不复制正文/输入摘要。评测和Trace读取类型头，旧兼容字典首次导入后不覆盖已建立类型记录。四类业务参数已另行迁移，其他改写/观察等兼容键仍未全部声明，AC-07-01仍未完成。

SQL恢复与for_next_turn共用carryover_context白名单；历史SQL context_data不是本轮证据/审查/模型计量/工具观察的来源。只保留必要背景与未完成参数，当前动作仍须SQL事实核验。该过滤只改变运行时投影，不删除历史记录。其他业务标识与观察兼容键的完整类型契约仍待收口，不能把白名单当全面类型化完成。

业务输入参数迁移：BusinessParameters严格冻结声明order_id/reason/refund_type/complaint_content四字段，订单长度32与现有OrderReadInput/RefundToolInput一致，原因/投诉内容上限10000与现有领域输入一致；可缺失/短原因代表待补齐，不改变既有6字补槽或资格规则。提取器必须只返回声明字段；当前类型参数权威、旧字典仅投影，明确清空订单不被旧字段复活。已建立参数贯穿请求已知对象、路由、改写、提示词、工具组装、持久动作恢复和下一轮白名单。动作展示已由RefundActionView另行迁移，其他业务编号/改写观察等全面类型化仍未结束。

确认观察迁移：TurnRefundConfirmation只记current_message/受控interrupt来源及本轮上下文摘要；摘要绑定user/actor/session/source_message_id、消息及完整预览。confirm_refund旧bool仅作兼容镜像，不再是节点确认来源。current_message来源只允许既有精确确认词；暂停来源由可信编排pause接口记录，参数/消息改变即失效，新轮次不继承。它不是签名或SQL事实，实际退款提交仍独立检查数据库当前消息、动作版本/金额/权限/资格与幂等。预览及动作展示已另行迁移，剩余兼容键契约仍未结束。

预览参数迁移：RefundPreview严格冻结声明order/reason/type/user/session/idempotency/action/version，允许缺参的旧展示上下文，但不含authorization、actor或其他控制键。setter完整验证归属后镜像，已建立预览的兼容字典变化或当前user/session变化会标invalid并使同轮确认失效；显式清空不会从旧字典复活。SQL动作恢复通过setter建立预览，节点与Run关联读类型投影。跨轮导入在深副本中进行，不修改旧状态对象；损坏的旧SQL预览不会让已提交回执恢复崩溃/重做。金额/状态显示已另行迁至RefundActionView并保持SQL事实来源，其他观察字段类型迁移尚未结束。


动作展示迁移：RefundActionView严格声明领域回执的ID/版本/订单/类型/原因、精确两位小数字符串金额、CNY、状态、ISO有效期与模拟标记。校验沿用既有金额/币种/模拟边界；pending/终态/过期分开，显示前必须与当前预览完整对应。元数据不完整/异常不向用户索取提交确认，不把ID+版本当可确认成功。镜像变化使确认失效，成功提交或取消清除本轮视图，不删除SQL事实。它只作数据/展示检查，不证明资格或支付；缺失视图的旧内部背景不能代替真实prepare/SQL当前消息。
