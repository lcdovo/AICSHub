# M1-13：模拟退换货申请的持久回执

- 日期：2026-11-03；基线：`5db3e5e0019ac1dc7e7e6e0975e8a7672d3536c3`。
- 对应 **OPT-08 / OPT-09、E01退款登记子集**；覆盖AC-08-02/05/07的本工具子集、AC-09-01/02/05/12的登记/查询子集。不能将本切片记为退款闭环或M1整体通过。
- 最终结果：本切片T0/同进程MCP检查通过；全量451 PASS / 2项M0既有FAIL，新增19项全部PASS。

## 问题与实现

原ApplyRefundTool在正常订单查询后只生成随机refund_id，没有持久申请，回复却承诺待审核、审核工作日、取件/短信和固定寄回地址。M1-01只阻断数据库查询异常的示例订单回退，未解决正常路径假成功。

本切片只登记**模拟待审核申请**，不执行支付、自动审核或履约：

1. 新增RefundApplication表及迁移0006，记录owner、actor、session、当前确认消息ID、订单、类型、原因、Numeric(12,2)金额、CNY、pending_review、is_simulated、唯一幂等键和参数摘要。申请与OperationAudit同一事务提交，只有真实持久记录才返回ID。
2. 内部apply_refund用途凭据120秒有效，绑定actor/owner、本轮已落库确认USER消息与完整参数。工具检查来源消息确为现有明确确认词、会话归属、当前操作人启用状态与owner权限；裸user_id、历史确认、模型confirm_refund、布尔身份或未知字段不能直接提交。凭据不写入工具历史/Trace/WS参数事件。
3. 保留原shipped/delivered/completed与订单全额口径。先完成只读上下文/资格校验及退出，保证读取依赖异常不生成申请ID；提交事务再校验身份、确认、订单资格与金额。MySQL订单读取加FOR UPDATE，真实执行/隔离行为尚未验收。
4. 申请金额用Decimal从现有订单Float投影的字符串转换，并检查有限正数、两位小数精度和列容量；订单投影字段未迁移。回执金额为两位小数字符串，模板支持该格式。不能据此保证真实支付账务或历史汇总精度。
5. 持久幂等作用域为actor/owner/原请求key，同键同参数返回原回执，改参数冲突。工具关闭进程成功缓存，只一次自动尝试；提交后回包丢失仍可能已提交，错误提示要求按原请求核查，不宣称确定未执行。预检仍要求当前资格，资格已变化可GET既有回执。
6. 输出明确is_simulated与模拟待审状态，移除虚构审核时限、短信、寄回地址/取件承诺。保留refund/return/exchange类型，仍只登记，未新增审批/支付流程。
7. 新增`GET /api/v1/refunds/{refund_id}`：本人/管理员可查，真实JWT下未认证401、越权403。没有新的普通HTTP提交入口、前端页面或管理员审批接口。

本轮保留原预览/本轮确认主流程，不引入新的资格时限、部分退款或跨会话重复申请业务规则。M1-01故障错误分类及不缓存/不重试保护保留；提交阶段故障现在提示结果待核查，不能泛称“未提交”。

## 回归证据

新增`tests/test_refund_persistence.py`最终19项：三种申请类型的持久模拟回执/审计/重放、无授权/他人订单/无本轮确认/不可退状态/篡改参数/布尔身份/未知字段/过期授权、10个同键并发只登记一次与改参数冲突、提交前回滚/提交后回包丢失、真实JWT本人/管理员/跨用户查询、SDK同进程MCP、实际LangGraph经REST/WS先预览不落库再确认提交、迁移保留订单。

图测试实际执行编排、工具、事务、模板和发布，意图/回答校验使用替身，不宣称真实模型质量。数据库使用独立SQLite，MCP是实际SDK内存传输，没有真实支付/模型或业务资料外发。新实例/会话重放不等于真实进程重启。

原`tests/test_refund_dependency.py`13项中，12种故障场景补合法合成上下文/签名，以穿过新增授权边界再注入工厂、会话进入、查询、退出故障；保留全部依赖分类、无回执/ID、无错误泄漏、不缓存/不重试断言。负向订单用例补真实合成主体、会话与确认，继续执行归属SQL查询。

| 执行 | 实际结果 | 原始证据（Git忽略） |
| --- | --- | --- |
| 修复前首批8项 | **6 FAIL / 2 PASS**，退出1，复现无模拟标记、无审计/持久化、裸身份及无确认仍成功 | `reports/offline/20261103-143106-928187/` |
| 首次接入授权后受影响集 | **50 PASS / 12 FAIL**：旧故障夹具先被授权拒绝，尚未执行其DB故障边界；不能忽略为基线 | `reports/offline/20261103-143246-498129/` |
| 合法故障上下文修正后 | **62 PASS** | `reports/offline/20261103-143319-065537/` |
| 扩展事务/MCP/查询集 | **67 PASS** | `reports/offline/20261103-143408-530767/` |
| `python scripts/test_offline.py -q --tb=short tests/test_refund_persistence.py tests/test_refund_dependency.py tests/test_refund_flow.py tests/test_chat_turns.py`（增加最后3个授权反例及格式归一前） | **70 PASS** | `reports/offline/20261103-143453-370126/` |
| 最终`python scripts/test_offline.py -q --tb=short tests` | **451 PASS / 2 FAIL**，退出1；0 skip/xfail，新增19项全部PASS，网络拦截数0 | `reports/offline/20261103-143610-547201/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-13-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

集合重叠，不累加；10次并发请求不是10个独立pytest。M0的`test_intent.py::test_low_confidence_keeps_llm_fallback`与`test_query_rewrite.py::test_no_previous_context_calls_llm`两项策略断言冲突仍保留，未修改它们或降低验收标准。保留原UTC/asyncio scope等弃用警告。

实测manifest是父提交及依赖/配置记录，执行时有本切片工作区变更。核心工作区SHA256（Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/agent/tools.py` | `8AA3DBF53E4710ED0EA86CD96DCD51725ACEFF0824C1B0DD9364FB5075790D64` |
| `app/agent/refund_authorization.py` | `78D322239D3170DF5B351120B99A6E965F395511C77CED60D6E4356B223986FC` |
| `app/services/refund_application.py` | `25C551CF4A82A2A00B14177A291E4A76E0DC71EB38C774F84A890EE682B5051E` |
| `app/models/models.py` | `B6CC408C56215E03CE858B5E511DDDFC747B38676FD3067DC937F46571FE593F` |
| `app/routers/refunds.py` | `CD5A58ECD029FA183D8BE697069E000AF8F31F33BE4146FDD8C27BBD00763319` |
| `tests/test_refund_persistence.py` | `8036B24D4FEBE0127257229A3120C3B175E29CE098CEB843195A4A35BEEA26E2` |
| `tests/test_refund_dependency.py` | `10088D5A361FB64A16E84AF5C1172C84762D691EC61C4B3D8534E0E7D0FFA38E` |

## 修改文件与未完成范围

- Agent：`app/agent/tools.py`、`refund_authorization.py`、`nodes/tool_node.py`、`nodes/response_node.py`、`mcp_client.py`、`mcp_server.py`。
- 持久化/接口：`app/models/models.py`、`app/services/refund_application.py`、`app/routers/refunds.py`、`app/main.py`、`alembic/versions/0006_refund_applications.py`。
- 测试：`tests/test_refund_persistence.py`、`tests/test_refund_dependency.py`。
- 文档：`docs/API.md`、`docs/AGENT_GRAPH.md`、`docs/DEPLOYMENT.md`、本报告。

**未完成门禁**：跨会话同订单重复申请/剩余可退额度、订单时限与部分退款、持久action_id/版本/有效期及预览参数精确确认、客户端重复消息去重、取消/审核/支付状态机、崩溃恢复仍未实现。不同key可能登记多份模拟申请；相同来源的签名只证明当前服务端参数绑定，不是完整action确认系统。历史订单号误路由政策咨询等理解风险不在本切片修复范围。

真实MySQL/Redis、多worker、stdio、实际进程重启/杀进程、浏览器、真实模型、支付/客服系统与部署回滚未验证。SQLite迁移只演练新增表upgrade/downgrade/upgrade，核对原订单不变，不代表生产备份恢复。不能将本切片启用为真实支付链路，也不能宣布AC-09整体通过。

无前端改动，不重复前端构建。未对业务库执行迁移、改写历史伪ID、删除数据或知识库/数据卷；static与原三个未跟踪文件保留，无外部服务费用、部署或推送。真实降级不能在新worker运行时删申请表或回退到假成功工具。

下一切片建议：持久退款action的预览版本/有效期与确认绑定（OPT-09、AC-09-05/07），涉及重复申请政策时再单独确认业务规则。当前提交后停止等待反馈。
