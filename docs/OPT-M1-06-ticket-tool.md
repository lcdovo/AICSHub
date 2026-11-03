# M1-06：聊天创建工单使用真实持久回执

- 日期：2026-11-03；基线：`abc96d89037a66b9dc35e29e260f3c245cf0d74b`。
- 对应 **OPT-08 / OPT-10、E01的创建工单子集；AC-08-01、AC-10-01的工单分支**。附带覆盖AC-08-02/03/05/07与AC-04-02的本工具子集。
- 结论：本切片T0与同进程MCP检查通过；真实MySQL、持久进程重启、stdio、浏览器和真实模型仍未验证，相关完整AC与M1整体未通过。

## 问题与实现

原`CreateTicketTool`只生成UUID、硬编码座席/SLA与成功话术，没有创建Ticket、通知或审计；管理员列表无法查到所谓成功工单。本轮改为与HTTP工单入口共用`ticket_creation.create_request`和`ticket_receipt`，使用既有表、事务与唯一约束；成功结果来自提交后的数据库记录。未分配/未设置SLA保持null，删除虚构分配与限时承诺，不声称通知已发送。

接入实际写入同时完成以下限定范围的执行边界：

1. REST/WS在本轮用户消息提交后，把消息ID与可信actor写入AgentState独立字段`source_message_id`/`actor_id`；for_next_turn不继承。用户消息、owner和actor均来自服务端；不从模型entities、collected_info或历史确认提取身份。
2. 工具节点签发120秒HS256内部凭据（复用AUTH_SECRET_KEY，无新配置），绑定owner、actor、来源消息ID和完整参数hash。稳定键为`chat-ticket:{session_id}:{source_message_id}`。凭据不放入工具历史/Trace参数或回复；工单异常不透传凭据和连接错误。
3. 直调/MCP执行前验证凭据签名、用途、到期与参数绑定，严格业务输入拒绝布尔user_id和未知字段；数据库验证actor存在且启用、来源消息存在且为本人会话USER消息。随后共享服务执行owner权限检查。管理员代聊的审计保留真实actor与目标owner。
4. 工单工具不使用BaseTool进程内成功缓存，最多一次自动执行尝试；持久唯一键与参数hash支持跨工具实例重放，参数变更冲突。提交后响应丢失时，只能按原键查回执，不能将失败当作确定未执行。其他工具保持原缓存/重试行为。
5. MCP create_ticket增加session_id/idempotency_key/authorization上下文。裸user_id不再授权建单；外部Host无内部凭据时应调用已认证HTTP入口。MCP初始化/回包异常的既有回退保留，本工具依靠同一持久键避免重复；不能推广为其他写工具的安全保证。

未改变投诉触发建单的既有规则，未新增退款/转人工/通知发送逻辑、模型供应商或数据库schema。参数限制沿用原工具类别与优先级枚举，新增内容上限10000字符及必需执行上下文。HTTP工单序列化只迁移到共享函数，响应字段保持原样。

## 验证证据

新增`tests/test_ticket_tool.py`最终26项。独立SQLite与合成用户/会话/消息；真实ASGI、实际工具/数据库事务，以及真实SDK同进程MCP内存传输。REST/WS图测试使用真实LangGraph编排和发布流程，意图分类与回答校验使用固定替身，未调用模型；认证边界沿用合成主体夹具，不冒充完整真实供应商或浏览器验收。

覆盖：回执能经用户/管理员API查询与列表展示；原键跨实例/HTTP重放；同键改参数不命中旧缓存；无凭据、篡改owner/content/session、非法字段/类型拒绝；无来源消息或非USER/他人消息、禁用actor拒绝；过期/跨用途凭据拒绝；10个并发请求只有一个工单/通知/审计；提交前回滚及提交后丢回包重放；MCP成功路径和模拟丢响应后直调；REST/WS实际图建单；管理员actor/owner审计；轮次字段不继承与凭据不进入工具记录。

| 执行 | 实际结果 | 原始证据（本机Git忽略） |
| --- | --- | --- |
| 修复前首批10项 | **10 FAIL**，退出1，复现无真实记录、无授权仍成功、故障假成功与旧缓存误命中 | `reports/offline/20261103-133404-710153/` |
| `python scripts/test_offline.py -q --tb=short tests/test_ticket_tool.py tests/test_ticket_idempotency.py tests/test_chat_turns.py tests/test_refund_flow.py tests/test_tool_retry.py` | **104 PASS**，退出0 | `reports/offline/20261103-133906-165935/` |
| `python scripts/test_offline.py -q --tb=short tests`（最终代码） | **294 PASS / 2 FAIL**，退出1；新26项全部PASS，0 skip/xfail，网络拦截数0 | `reports/offline/20261103-134015-908672/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-06-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

集合重叠，不累加。补齐管理员actor前曾做中间全量：293 PASS/2 FAIL（`20261103-133741-454006`），不替代最终结果。两项既有失败仍为`test_intent.py::test_low_confidence_keeps_llm_fallback`与`test_query_rewrite.py::test_no_previous_context_calls_llm`，策略断言冲突原因见M0；没有改断言或降低AC。保留原UTC、asyncio、multipart及SDK依赖弃用警告。

最终manifest为父提交及环境记录，实测时有本切片工作区改动。核心实测工作区SHA256（Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/agent/ticket_authorization.py` | `8420E56B79A086F02A0E3EE501266A828E19C49F2AD86426ECAF741658CAF003` |
| `app/agent/tools.py` | `3D208AF2FD3ABF028A18D3C55B8F35A21533183B1F55129AE3219C5622BE93D3` |
| `app/agent/nodes/tool_node.py` | `4D569F0FEAF9DCB0E4D7852C838A5BCEF432596C394361AE1FA271FF68D4B954` |
| `app/agent/state.py` | `3646FFB84F11BB703AA997BD26FDA4AAC3C488D7E7991009B724501527DB6E68` |
| `app/routers/chat.py` | `74E860F64CED409D5E51CAF509D452BE6CC0FA76B8EEEC5E79560F88A8BA04D8` |
| `tests/test_ticket_tool.py` | `960A67574AF0DE9506D4464EC1AED3562742202E911DC39B32A42AD8BC14A372` |

## 修改文件

- Agent：`app/agent/tools.py`、`ticket_authorization.py`、`state.py`、`nodes/tool_node.py`、`mcp_server.py`、`mcp_client.py`。
- 路由与服务：`app/routers/chat.py`、`app/routers/tickets.py`、`app/services/ticket_creation.py`。
- 测试：`tests/test_ticket_tool.py`。
- 文档：`docs/API.md`、`docs/AGENT_GRAPH.md`、本报告。

## 限制与后续

- 未验证真实MySQL/Redis、多worker、stdio子进程、实际进程重启/崩溃、浏览器、真实模型或部署/回滚。新实例重放不是实际重启验收；模拟丢响应不等于真实网络故障。10并发只为本工具T0补充，不能替代T1/T4重复故障矩阵。
- 内部凭据是短期bearer执行授权，依赖强AUTH_SECRET_KEY与受信签发节点，不是独立对外MCP身份系统。已签发凭据不绑定access token_version，账号禁用/角色变化会重新检查，但不承诺运行中注销即时取消已开始动作。
- 同一消息ID键只能保护本次工具执行重放。客户端重复发送会生成新消息ID，仍可能产生另一工单；消息去重、持久Run、动作确认、答案持久化崩溃窗口与历史恢复尚未解决。
- E01尚未整体关闭：退款申请、通知发送、更新工单及内存人工协同仍有剩余假成功/一致性问题，不把创建工单修复推广到它们。
- 无前端变更，本轮未重复构建/浏览器验收；没有主库/知识库/数据卷变更、外部模型费用、部署或推送；原三个未跟踪文件保留。
- 下一切片建议：更新工单工具的真实状态变更与权限（OPT-08/10、AC-10-02/03），复用现有状态转换规则，不顺带修改退款/人工流程。提交后停止等待反馈。
