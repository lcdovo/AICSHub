# M1-12：解决人工请求的状态幂等与审计

- 日期：2026-11-03；基线：`3fb47bd856f9b5e4fecbd43253213b5ef9f114ea`。
- 对应 **OPT-10、AC-10-07的解决请求分支**，附带覆盖AC-10-04/10的持久结果与本人查询子集。重新分配与实际进程重启不在本轮保证范围。
- 最终结果：本切片T0检查通过；全量432 PASS / 2项M0既有FAIL，新增20项全部PASS。

## 问题与变化

原resolve入口每次覆盖resolution和resolved_at，还会把传入agent_id写到assigned_to，形成隐式重分配；没有操作审计，重复请求会重写原处理事实。

新增`handoff_resolution.resolve_request`：

1. 保持管理员入口，并在服务层重查账号状态/角色。处理结果去除首尾空白后长度1至10000；agent_id可省略，提供则必须等于已有assigned_to，不能把未分配/他人座席写成新处理人。
2. 初次解决按原状态、指派、resolution与resolved_at做条件更新，结果和resolve_handoff审计同事务提交。审计保存actor、owner、原座席、原状态及提交结果/时间。保留原响应request_id/resolution字段。
3. 同结果重复解决直接返回原终态，不改解决时间、不新增重复审计；不同结果409、不同座席409。竞争条件失败先结束旧事务重读：同结果胜者可重放，其他结果冲突，不靠进程缓存。
4. 原来已解决但无审计的历史记录可按原结果重放，不补造历史审计。未解决状态的旧许可范围保持，包括pending/cancelled；没有擅自收紧为仅assigned可解决。后续若要变更这些业务规则需单独确认。
5. 抽取已有`lock_capacity_gate`供解决与分配共用。有原座席时先取得该座席数据库闸门，解决事务提交后ASSIGNED计数下降，不需要手工减内存计数或容量计数器。没有新增表或迁移。

本轮不新增重新分配、纠错、真实客服接通或通知发送。API契约已同步，不把“已解决请求重放”表述为新的处理动作。

## 验证与证据

新增`tests/test_handoff_resolution.py`最终20项；真实ASGI/临时SQLite，认证使用合成数据库主体夹具，服务当前管理员校验真实执行。全部输入为合成请求，无真实用户数据、模型或外部依赖。

覆盖重复解决时间/结果/单审计、结果变更冲突、已有/未分配请求的agent归属断言、空/空白/超长结果、普通用户拒绝、并发同结果/不同结果、提交前回滚/提交后回包丢失重放、历史无审计记录、原pending/cancelled许可范围、缺失请求404、解决后容量再次可用、actor/owner审计、当前管理员禁用/降级不能重放。

| 执行 | 实际结果 | 原始证据（本机Git忽略） |
| --- | --- | --- |
| 修复前首批9项 | **7 FAIL / 2 PASS**，退出1，复现重复覆盖、无审计、agent_id改写及无效结果入库 | `reports/offline/20261103-142409-902385/` |
| 首批修复 | **9 PASS** | `reports/offline/20261103-142451-627318/` |
| `python scripts/test_offline.py -q --tb=short tests/test_handoff_resolution.py tests/test_handoff_assignment.py tests/test_handoff_capacity.py`（增加当前管理员2项前） | **58 PASS**，退出0 | `reports/offline/20261103-142532-365752/` |
| 最终`python scripts/test_offline.py -q --tb=short tests` | **432 PASS / 2 FAIL**，退出1，0 skip/xfail，新增20项全部PASS，网络拦截数0 | `reports/offline/20261103-142626-259376/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-12-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

集合重叠，不累加。M0既有两项策略断言冲突仍保留：`test_intent.py::test_low_confidence_keeps_llm_fallback`与`test_query_rewrite.py::test_no_previous_context_calls_llm`；未修改断言/策略或降低验收标准，保留原UTC/asyncio等弃用警告。

manifest为父提交及配置记录，实测时有本切片工作区变更。工作区SHA256（Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/services/handoff_resolution.py` | `CEEA73EB331C52A1C52AF6343412DE94AB0E936B267448B466253D978578E418` |
| `app/services/handoff_assignment.py` | `774CA1102ED9DE6E86AEB1BD7C5C0ABD055A168BA0389B41B3A107C358EC89C0` |
| `app/routers/tickets.py` | `24F578C9A3D62B1FAC8461EC68D242D115EE1708AB97F5D2FD7969145ACFFC04` |
| `tests/test_handoff_resolution.py` | `FA9C11F341332D5AB2BE632DC69C33865D2FD8BB8163E6A47C518170FDC920B1` |

## 修改文件及边界

`app/services/handoff_resolution.py`、`app/services/handoff_assignment.py`、`app/routers/tickets.py`、`tests/test_handoff_resolution.py`、`docs/API.md`、本报告。

未验真实MySQL、多进程、实际重启/杀进程、Redis故障、浏览器、真实客服状态、部署/回滚；新数据库会话和提交异常替身不等于完整恢复。AC-10-07包含重新分配和进程重启，这些仍NOT_RUN，不能宣布整个AC或M1通过。

未改变尚未解决状态的业务许可范围，取消后是否可解决仍是后续业务规则问题。审计只记录本轮成功状态转移，不虚构历史缺失记录、不为重复读取生成第二条“解决”事件。旧座席名册与统计边界沿用前轮，不扩大为真实客服系统。

没有前端变更，不重复前端构建或浏览器验收。无主业务库、知识库、数据卷、static修改，无外部调用费用、部署或推送；原三个未跟踪文件保留。

下一切片建议回到剩余P0：退款申请正常路径的假成功与持久申请回执（OPT-08/09）；先冻结现有申请/确认能力边界，不扩展真实打款。当前提交后停止等待反馈。
