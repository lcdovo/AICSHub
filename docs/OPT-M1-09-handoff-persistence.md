# M1-09：人工请求持久登记与真实回执

- 日期：2026-11-03；基线：`811013d6851b141deee57557cc7ed474a9a2e96b`。
- 对应 **OPT-08 / OPT-10、E01/E03的请求创建子集、AC-08-01人工分支**；附带覆盖AC-08-02/03/05/07的本工具子集及AC-04-01查询归属。
- 本切片不等于人工协同完整验收：座席分配/负载、实时接通、真实依赖及恢复仍未完成。
- 最终结论：本切片T0及同进程MCP检查通过；全量372 PASS / 2项M0既有FAIL，新增26项全部PASS。

## 问题与实现

原工具在内存CollaborationService中登记/自动分配请求，HTTP列表却查询数据库；异常分支仍返回随机ID、排队位置与等待时间。图节点在工具失败/data为空时可能抛错，或把响应改成“已转接、预计等待3–5分钟”；模板也会补造默认等待时间。

1. 工具与HTTP创建人工请求共用`ticket_creation.create_request`及`handoff_receipt`。请求、通知事件和actor/owner审计在同一事务提交。初始状态pending、assigned_to为空；返回真实request_id并保留escalation_id兼容别名。原HTTP优先级/SLA规则不变，SLA不等于接通时间。
2. 新建`handoff_authorization.py`，内部request_handoff用途凭据120秒有效，绑定actor、owner、本轮已落库USER消息ID和完整参数摘要。图节点只使用AgentState可信字段签发，不从历史/模型实体取身份；凭据不写工具历史/Trace。执行时复核账号状态、来源消息及会话归属，调用共享owner权限检查。
3. 持久键为`chat-handoff:{session_id}:{source_message_id}`，不使用进程成功缓存，最多一次自动尝试；同键同参数重放现有记录，改参数冲突。MCP接口增加幂等键/内部凭据上下文，不再接受裸user_id作为写入授权。
4. 工具context原字符串映射为数据库`{}`或`{"context": text}`，结果context为持久对象。工具priority保留normal/urgent，HTTP原high/low不变。图节点转接原因限定为100字符以匹配现有数据库列，保留原优先级选择。
5. 图节点记录handoff_attempted并终止本轮；工具缺失、失败、异常或回执结构/归属无效均明确未确认，不发布已接通/排队成功。模板只按真实ID和状态生成说明，不使用工具返回的任意成功话术补造等待。
6. 新增`GET /api/v1/handoff/requests/{request_id}`：本人或管理员可查，未认证401、越权403、不存在404。管理员列表原路径与响应保持。

未新增自动座席分配、实时人工聊天、通知发送或前端界面，也没有schema变更/数据迁移。旧内存请求未迁移；新创建路径不再调用CollaborationService.execute_handoff，但旧内存座席列表/统计等仍存在，不能代表持久请求的排队/接通状态。

## 验证与证据

新增`tests/test_handoff_persistence.py`最终26项，使用独立临时SQLite、合成用户/会话/消息、实际工具事务、真实ASGI与SDK同进程MCP。图流程使用实际LangGraph，意图识别替身冻结为human；没有真实模型。图集成认证沿用合成主体夹具，新增详情入口另用真实JWT依赖验证401/200/403。

覆盖：持久请求/通知/审计、管理员列表与本人详情、跨用户拒绝、API/工具同键重放、缺凭据/篡改owner/session/reason/类型/未知字段、过期/跨用途/缺消息/禁用actor、数据库故障、提交前回滚/提交后丢回包重放、同键改参数不命中缓存、10并发只创建一次、同进程MCP、REST/WS实际图、管理员actor/owner、失败/缺工具/缺回执/非法状态不改写成功、模板不编造等待。

| 执行 | 实际结果 | 原始证据（本机Git忽略） |
| --- | --- | --- |
| 修复前首批12项 | **12 FAIL**，退出1；复现内存记录不入库、异常假成功、失败话术覆盖及模板虚构等待 | `reports/offline/20261103-135927-605903/` |
| 初始修复及聊天/路由 | **71 PASS** | `reports/offline/20261103-140110-842979/` |
| 扩展人工/幂等/聊天/退款/路由集 | **117 PASS**（新增真实JWT详情断言前） | `reports/offline/20261103-140249-429226/` |
| 首次全量 | **370 PASS / 3 FAIL**；其中新增1项为测试误用AsyncClient.app，不能归为应用缺陷；另2项为M0既有失败 | `reports/offline/20261103-140436-898934/` |
| 夹具修正后人工/聊天/路由集 | **85 PASS**，退出0 | `reports/offline/20261103-140659-541796/` |
| 最终`python scripts/test_offline.py -q --tb=short tests` | **372 PASS / 2 FAIL**，退出1；新增26项全部PASS，0 skip/xfail，网络拦截数0 | `reports/offline/20261103-140756-791225/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-09-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

测试夹具错误已明确修正：真实JWT检查改用已有TestClient认证夹具，不改业务代码或降低401/200/403断言。单例重现错误证据`20261103-140607-556243`保留。最终结论仅采用修正后的全量结果，不能将上表带新失败的执行当作通过。

集合重叠，不累加。M0既有两项失败：`test_intent.py::test_low_confidence_keeps_llm_fallback`及`test_query_rewrite.py::test_no_previous_context_calls_llm`，策略断言冲突原因见M0；没有修改断言、策略或验收标准。保留既有UTC、asyncio scope、multipart和SDK依赖弃用警告。

实测manifest记录父提交与环境，执行时有本切片工作区变更。核心源码SHA256（工作区字节，Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/agent/handoff_authorization.py` | `2751B68EE058550E0D65CD1CCD3EE213987976B26092F4A9E5F88517FEC125FA` |
| `app/agent/tools.py` | `1A14D69883D5144298A7F338482E5BD699F2F76200BAB096140F397555A6AD4C` |
| `app/agent/nodes/handoff_node.py` | `6C857DAA79AC35D15F2E03AEA7043FC609906A9F3E42B8442ADEF37B17C071E8` |
| `app/agent/nodes/response_node.py` | `45EED2E5B537E3BE89CB5478B320594AB1EF219D27967CFC586A30A3666E8991` |
| `app/services/ticket_creation.py` | `42916F63E73F4F7C3DFFF7E3BD5E2479DECF978A7F95B0FEB60835021192BB5D` |
| `app/routers/tickets.py` | `2D30801FE2132A6A0DA532D757A0DCEC7A6C18979D60184349879D92BA207797` |
| `tests/test_handoff_persistence.py` | `2D5E95D4678B4ABC8EC0C60D580D5704C098A8A8155763C8839B04F70AEDACC9` |

## 修改文件与范围限制

- 工具/协议：`app/agent/tools.py`、`handoff_authorization.py`、`mcp_client.py`、`mcp_server.py`。
- 节点：`app/agent/nodes/handoff_node.py`、`response_node.py`。
- 路由/共享服务：`app/routers/tickets.py`、`app/services/ticket_creation.py`。
- 测试：`tests/test_handoff_persistence.py`。
- 文档：`docs/API.md`、`docs/AGENT_GRAPH.md`、本报告。

真实MySQL/Redis、多worker、stdio、实际进程重启/崩溃、浏览器、真实模型、部署回滚未验证；新实例/故障替身不能当真实恢复。10并发只是T0补充，不替代T1/T4重复故障矩阵。内部凭据未绑定access token_version，不能承诺注销即时取消已开始动作；同源消息重放不等于客户端重复帧/多消息意图去重。

分配/解决接口对座席存在、负载、状态CAS和审计仍待独立验收，不宣称AC-10-04/05/06/07完整通过。新详情API可查真实请求，不代表用户界面已有进度组件。退款申请仍有假成功/持久化缺口，E01未整体关闭。

没有主业务库、知识库、数据卷、static变更，没有外部模型费用、部署或推送，原三个未跟踪文件保留。下一切片建议：人工分配的座席存在/可用与状态变更校验（OPT-10、AC-10-05），先核实现有内存座席与持久请求边界，独立冻结可支持能力。提交后停止等待反馈。
