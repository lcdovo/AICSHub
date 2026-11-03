# M1-07：工单更新的真实状态、权限与回执

- 日期：2026-11-03；基线：`56d9374a7b9c338917cc62eb32f4d5a85229f233`。
- 对应 **OPT-08 / OPT-10、E01的更新工单子集；AC-10-02、AC-10-03**。附带覆盖本工具的参数、权限、持久幂等和协议一致性检查，不把它推广为其他工具验收。
- 结论：本切片T0/同进程MCP检查通过；真实MySQL、多worker和完整恢复未验证，完整AC与M1整体仍为部分验收。

## 问题与实现

原`UpdateTicketStatusTool`没有查询或更新数据库，对不存在工单也返回成功，old_status固定为pending，没有操作人认证与权限检查。HTTP更新已有限管理员与状态转换规则，但与工具无共享业务入口。

本轮新增`app/services/ticket_update.py`，两个入口共用更新事务：

1. 当前数据库actor必须存在、启用、disabled_at为空且role为admin。普通用户不能改本人或他人工单，且先检查角色再查资源，避免探测不存在工单。目标新请求不存在404，非法跳转409。
2. 将原API状态转换表原样移到共享服务；保留API的priority/assigned_to/resolution与相同状态更新行为。验证全部字段后才写，不为使测试变绿改状态规则。
3. 工单更新与OperationAudit原子提交，审计保存actor、owner、参数hash、note、实际old/new状态和提交时快照。note仅作为操作审计备注，不代写resolution。数据库异常不构造“已更新”，提交后回包丢失仍可能已经执行，应按原键核查。
4. 以原status、priority、assigned_to、resolution、resolved_at、updated_at和user_id为条件更新；字段已变化则409，审计整体回滚。未新增版本列；此策略不等于完整版本化并发控制。
5. API新增可选Idempotency-Key请求头；工具要求非空幂等键。作用域包含actor、ticket_id与操作键，参数变更冲突；同键返回原操作提交回执，不重复写入或改时间。若后来有另一更新，旧回执不代表当前状态，最新状态通过详情查询。
6. MCP更新工具增加authorization/idempotency_key，凭据限定update_ticket_status用途、120秒有效、绑定actor与参数摘要；创建工单凭据和裸actor_id不能替代。执行时重查当前管理员角色。工具不使用进程内成功缓存，仅一次自动执行尝试；MCP异常日志不透传凭据。

没有改变普通聊天路由：它原本不选择更新工单工具，本轮仍不自动选择或签发更新凭据。内部签发函数供受信管理员适配使用，没有新增对用户开放的凭据签发接口；外部客户端使用已认证管理员HTTP更新入口。未扩大普通用户权限、接入模型或外部渠道，也没有schema迁移。

## 回归与证据

新文件`tests/test_ticket_update_tool.py`最终32项，独立临时SQLite与合成数据。ASGI更新/查询走实际API，认证主体使用合成数据库夹具；工具签名验证和数据库事务真实执行，MCP使用真实SDK内存传输，没有真实模型调用或对外服务费用。

覆盖：缺失工单、普通用户本人/他人工单、3种非法跳转、3种合法前后状态、当前管理员禁用/降级、缺失/篡改/过期/跨用途凭据、未知字段、依赖失败、API原有字段、无效混合参数不部分写入、API/工具同键重放、后续更新后旧键不回退状态、同键改参数、提交前回滚/提交后丢响应、10并发同键一次审计、不同键读取相同原状态后的确定性CAS竞争、同进程MCP合法更新。

| 执行 | 实际结果 | 原始证据（本机Git忽略） |
| --- | --- | --- |
| 修复前首批12项 | **12 FAIL**，退出1，复现无实际更新、假不存在成功、无权限/非法跳转仍成功、依赖假成功 | `reports/offline/20261103-134349-008487/` |
| `python scripts/test_offline.py -q --tb=short tests/test_ticket_update_tool.py tests/test_ticket_idempotency.py tests/test_ticket_tool.py tests/test_tool_retry.py`（增加另外2个合法状态变体前） | **93 PASS**，退出0 | `reports/offline/20261103-134623-328016/` |
| `python scripts/test_offline.py -q --tb=short tests`（最终源码/测试） | **326 PASS / 2 FAIL**，退出1；新增32项全部PASS，0 skip/xfail，网络拦截数0 | `reports/offline/20261103-134821-561388/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-07-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

集合重叠，不累加。两项既有失败仍为`test_intent.py::test_low_confidence_keeps_llm_fallback`和`test_query_rewrite.py::test_no_previous_context_calls_llm`，策略断言冲突见M0；没有改断言、策略或验收标准。保留原UTC、asyncio scope、multipart和SDK依赖弃用警告。

manifest记录父提交与环境，实测时存在本切片工作区改动。实测工作区SHA256（Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/services/ticket_update.py` | `F873799ABED7AD5CB63D2B7D40024398984907C1F7F05B8EDD42A974E53684E2` |
| `app/agent/tools.py` | `13E1E81423AD643A08C9BC6EFAAD5B4CA78F441FCCDBBBF3BE8DA38B98F655E5` |
| `app/agent/ticket_authorization.py` | `0796699837646ECEA729AB510732F34242CF23581DE47A223BC04251A4E34C05` |
| `app/routers/tickets.py` | `A1E0EF27575DE38BBF1579DAA59B02DBE013E363B34959E45E8E83CED82038CD` |
| `tests/test_ticket_update_tool.py` | `C0FB63B4B7470F4C981572C29605707EAF764860F78690F84BD9C06E7ECF135B` |

## 修改文件与边界

- 服务/路由：`app/services/ticket_update.py`、`app/routers/tickets.py`。
- 工具/凭据/协议：`app/agent/tools.py`、`app/agent/ticket_authorization.py`、`app/agent/mcp_server.py`、`app/agent/mcp_client.py`。
- 测试：`tests/test_ticket_update_tool.py`。
- 文档：`docs/API.md`、`docs/AGENT_GRAPH.md`、本报告。

真实MySQL事务/时间精度、多worker、stdio、进程重启/崩溃、浏览器、真实模型、部署和回滚未验证。单次10并发与确定性条件竞争只是T0补充，不替代T1/T4重复故障矩阵；没有单调版本列，不能宣称完整CAS/ABA保护或取消已开始动作。内部短期凭据不绑定access token_version，但执行会检查账号与当前角色，不承诺运行中注销即时取消。

没有前端变更，本轮未重复前端构建/浏览器验收。工单分配字段仍沿用原API，不新增座席存在/负载规则。退款、转人工、通知工具剩余假成功与未知结果风险仍待修；不宣布E01整体关闭。

业务数据库、知识库、数据卷、static输出和原三个未跟踪文件保留；无真实外部费用、部署或推送。下一切片建议：未配置通知渠道时不得声称已发送（OPT-08、AC-08-02），独立核实通知队列/模拟发送边界。提交后停止等待反馈。
