# M1-10：人工分配的座席准入校验

- 日期：2026-11-03；基线：`462d6b7d3d9692e03cf644eabc5216947cf93f13`。
- 对应 **OPT-10、AC-10-05**；附带覆盖同一请求的条件写入与管理员权限子集。
- 仅验收当前演示名册下的分配准入；真实座席状态、持久容量协调与AC-10-06不在本切片保证范围。
- 最终结果：本切片T0检查通过；全量389 PASS / 2项M0既有FAIL，新增17项全部PASS。

## 问题与实现

原`POST /api/v1/handoff/{id}/assign`对任意agent_id直接写ASSIGNED，既不查座席，也不查在线状态或负载。座席列表来自进程内HumanAgentService演示名册，current_load不包含数据库里的人工指派。

新增`app/services/handoff_assignment.py`：

1. 服务层再次读取操作人当前启用状态/disabled_at/管理员角色；原HTTP管理员限制保留。请求不存在404，非pending请求409，拒绝不改请求或审计。
2. 从当前名册读取座席，不存在404、status不是online则409。容量沿用原max_load；无效容量或负载达到容量时409。没有新增座席启用/管理接口、实际在线检测或业务容量规则。
3. 持久负载只统计ASSIGNED的HandoffRequestRecord，RESOLVED/CANCELLED不占用该计数。准入负载取旧内存current_load与持久计数较大值；`GET /api/v1/agents`使用同一口径并保留原字段和筛选。只做分组查询，不逐座席查库。
4. 新分配不再依赖或增减内存计数；数据库指派进入解决状态后，持久负载自然下降，避免新指派额外遗留一份内存负载。已有旧内存负载不清空或篡改，两个来源无法准确去重，不宣称较大值是真实总负载。
5. 更新以pending、原assigned_to/assigned_at为条件，阻止同一请求被两个分配覆盖。请求指派与OperationAudit同事务提交，审计保存actor、owner、agent和提交时间；失败回滚两者。成功响应仍保留原request_id/agent_id。

无schema迁移、数据回填或历史指派修改。解决/重新分配接口保留原规则；不接入真实座席系统、不增加通知发送，也没有前端变更。

## 回归与证据

新增`tests/test_handoff_assignment.py`共17项，真实ASGI与临时SQLite，认证使用合成数据库主体夹具、座席名册使用独立合成HumanAgentService。测试不是实际真人状态或多worker验收。

覆盖不存在/disabled/offline/busy座席、内存满载、DB满载、合法分配与原子审计、列表负载、解决记录不计容量、3种非pending状态、普通用户拒绝、缺失请求、提交失败回滚、两个座席同步竞争同一请求只有一赢家、列表skill/status筛选。

| 执行 | 实际结果 | 原始证据（本机Git忽略） |
| --- | --- | --- |
| 修复前首批13项 | **10 FAIL / 3 PASS**，退出1；复现无效/不可用/满载仍成功、无审计及非pending错误契约 | `reports/offline/20261103-141122-900599/` |
| 首批修复 | **13 PASS** | `reports/offline/20261103-141200-319128/` |
| `python scripts/test_offline.py -q --tb=short tests/test_handoff_assignment.py tests/test_handoff_persistence.py tests/test_ticket_idempotency.py` | **73 PASS**，退出0 | `reports/offline/20261103-141241-856339/` |
| 最终`python scripts/test_offline.py -q --tb=short tests` | **389 PASS / 2 FAIL**，退出1；0 skip/xfail，新增17项全部PASS，网络拦截数0 | `reports/offline/20261103-141340-340403/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-10-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

集合重叠，不累加。M0两项既有失败为`test_intent.py::test_low_confidence_keeps_llm_fallback`和`test_query_rewrite.py::test_no_previous_context_calls_llm`，策略冲突见M0，未修改断言或降低验收标准。

实测源码SHA256（工作区字节，Git可能规范化行尾；manifest记录父提交与依赖环境）：

| 文件 | SHA256 |
| --- | --- |
| `app/services/handoff_assignment.py` | `EDC2E11D153238C74A4011250FE6A2A948B05DDB8C061AC2017B1E163CF8D6CE` |
| `app/routers/tickets.py` | `4461D25C86C5981D0102B6C04BD207DD4F4991D52A83DC72F53F992166B306AF` |
| `tests/test_handoff_assignment.py` | `0113BCD79F4D45BFF527882B6EB5CE1D8D1369163BD0C048A8EE7F9D75F20D22` |

## 修改文件与限制

`app/services/handoff_assignment.py`、`app/routers/tickets.py`、`tests/test_handoff_assignment.py`、`docs/API.md`、本报告。

**当前仍有容量竞争缺口**：负载读取后，不同请求可同时看到空余名额并提交，未实现座席级持久容量预留/锁；AC-10-06仍未通过。名册只在进程内，跨worker状态可漂移；内存与DB集合也无法去重，取较大值不能保证覆盖两个来源的全部真实工作。没有把条件更新同一请求的成功推广为容量竞争安全。

未验真实MySQL/Redis、多worker、实际进程重启、代理、浏览器、真实客服状态/模型、部署或回滚。旧内存协同统计不是持久指派统计。解决、重新分配、状态幂等和追加审计完整性仍需独立切片，不能宣布AC-10-04/07已通过。

本轮无前端修改，不重复前端构建；保留既有UTC等弃用警告。业务数据库、知识库、数据卷、static和原三个未跟踪文件保留；无真实外部费用、部署或推送。

下一切片建议：持久座席名册/容量预留与同一名额并发争抢（AC-10-06），先按当前演示容量冻结迁移及历史指派兼容策略；或独立处理解决请求的状态/审计一致性。当前提交后停止等待反馈。
