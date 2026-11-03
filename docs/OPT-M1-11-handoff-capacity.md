# M1-11：座席容量的持久事务闸门

- 日期：2026-11-03；基线：`9880805f3730a487dfaf3f52cf0608ae8e15d7a7`。
- 对应 **OPT-10、AC-10-06**；本轮只覆盖一致演示名册配置下的事务容量争抢，不等于真实座席系统/生产多worker验收。
- 最终结果：本切片离线检查通过，全量412 PASS / 2项M0既有FAIL，新增23项全部PASS；真实依赖门禁仍未执行。

## 问题与实现

原分配流程先读取负载再提交指派，不同请求会同时看到最后一个空余名额。修复前隔离反例使用10个请求、两个独立引擎/会话工厂和独立座席对象，最终10个全部成功，超过容量1。

新增`HandoffCapacityGate`及迁移`0005_handoff_capacity`：

1. 闸门按agent_id主键持久存储，分配时以SQLite upsert / MySQL ON DUPLICATE KEY UPDATE原子创建或递增revision并取得数据库写锁。闸门不是占用计数器，不把进程内锁当跨worker协调。
2. 同事务中在取得座席闸门后读取当前ASSIGNED指派，再按原max_load和内存负载口径判断。MySQL查询使用FOR UPDATE当前读，避免本事务先前身份/请求查询的旧快照；真实MySQL隔离与锁行为仍需T1验证。其他数据库类型明确拒绝，不偷偷无锁执行。
3. 闸门、请求条件更新和审计同事务提交/回滚，锁持有到事务结束。已有历史ASSIGNED参与统计，无需回填占用计数；解决后不再占用，失败事务不预留名额。revision只用于触发数据库写锁，不是业务容量或请求版本。
4. 沿用现有名册、online校验、max_load和effective_load口径，不新增座席业务规则、不改解决/重分配流程。所有写入worker须升级且名册/容量配置一致；旧直接指派路径或配置漂移可绕过该保证。
5. 原同一请求竞争测试的barrier移至读取请求后、闸门前，仍断言200/409与唯一审计；如果放在已取得座席锁后的UPDATE前，两请求会被测试同步逻辑互相阻塞，不能据此改变业务串行顺序。

本轮只新增闸门表，**没有持久化座席名册**、回填演示座席、改写历史指派或在业务库运行迁移。API及部署说明已补充先迁移再切写流量、禁止新旧混跑及安全降级边界。

## 回归与证据

新增`tests/test_handoff_capacity.py`共23项：20轮×每轮10请求争抢容量1，两个独立SQLAlchemy引擎/会话工厂、独立名册对象，必须每轮1成功/9冲突、1个ASSIGNED及1条审计；另验证解决释放名额、失败事务不占名额、新增迁移保留历史指派。

迁移检查只在独立SQLite建升级前结构，保留历史assigned_to含未知老座席ID，执行upgrade两次、downgrade、再upgrade，核对原记录不变；不是业务库迁移、备份恢复或完整历史Alembic链验收。原0001使用当前metadata初始化空库，故0005兼容已存在闸门表。

| 执行 | 实际结果 | 原始证据（Git忽略） |
| --- | --- | --- |
| 修复前最后名额反例 | **1 FAIL**，观察10成功而非1成功 | `reports/offline/20261103-141705-542232/` |
| 首次修复集 | 17 PASS / 1 FAIL；竞争本身通过，测试barrier错误拦住最终snapshot读取并超时 | `reports/offline/20261103-141737-433628/` |
| 扩展集旧进程结果 | 39 PASS / 1 FAIL，同一测试钩子超时；证据保留，不能算应用缺陷或通过 | `reports/offline/20261103-141820-594031/` |
| `python scripts/test_offline.py -q --tb=short tests/test_handoff_capacity.py tests/test_handoff_assignment.py`（最终upsert与修正测试） | **40 PASS**，退出0 | `reports/offline/20261103-141908-487989/` |
| 最终`python scripts/test_offline.py -q --tb=short tests` | **412 PASS / 2 FAIL**，退出1，0 skip/xfail，新增23项全部PASS，网络拦截数0 | `reports/offline/20261103-142014-502616/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-11-compileall.log` |
| MySQL方言SQL编译 | upsert及FOR UPDATE语句编译成功，退出0；不是MySQL执行 | `reports/offline/m1-11-mysql-sql-compile.log` |
| `git diff --check` | PASS | 提交前检查 |

集合重叠，不累加；20轮共200次竞争请求不冒充200项pytest。测试钩子修复在结果读取前恢复原get方法，不弱化竞争结果/审计断言。M0既有失败仍为`test_intent.py::test_low_confidence_keeps_llm_fallback`和`test_query_rewrite.py::test_no_previous_context_calls_llm`，未改断言与验收标准，保留原UTC、asyncio scope等弃用警告。

实测manifest为父提交与依赖配置，执行时有工作区变更。核心工作区SHA256（Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/services/handoff_assignment.py` | `A7C5C61222B21468984815B935FB505FEFDEA030A58B2E88D8B36427A252C67D` |
| `app/models/models.py` | `48F3BAE7E76F565307997F076002EB0C9D55F5E089BBD4621DAAB2C00D2FB7CF` |
| `alembic/versions/0005_handoff_capacity.py` | `54EBBFC5783E6AD71B5614911210CEB2CC10C4D4FC817755367FA339A1F11895` |
| `tests/test_handoff_capacity.py` | `F4A747FEE4C1593453B004182ECF1F72661FD9423A471CA6AA84B1DE972D5A5D` |
| `tests/test_handoff_assignment.py` | `C63FE0093F053BE83599C10B75C0E4D01FCA45202B910CC1E541A6242ECCC8FE` |

## 修改文件与边界

`app/models/models.py`、`app/services/handoff_assignment.py`、`alembic/versions/0005_handoff_capacity.py`、`tests/test_handoff_capacity.py`、`tests/test_handoff_assignment.py`、`docs/API.md`、`docs/DEPLOYMENT.md`、本报告。

两个独立引擎不是两个实际进程。未验真实MySQL/Redis、双worker、服务重启/杀进程、故障注入矩阵、浏览器、真实客服状态或部署回滚；因此AC-10-06完整真实依赖门禁仍NOT_RUN，不能宣布生产容量保证。SQLite写事务会串行所有写入，其吞吐不能外推MySQL。

名册和容量策略仍在内存，所有worker必须配置一致；不把闸门表当持久名册。旧内存工作与持久ASSIGNED无法完全去重，effective_load较大值不是两个集合真实总和。解决、删除、重新分配和未知旧座席的完整生命周期仍待单独处理。

没有业务数据库迁移、数据删除、外部调用费用、部署或推送；原三个未跟踪文件、知识库、数据卷与static保留。新增迁移downgrade会删除闸门表，仅在本次临时库执行；真实降级须停分配写入，不能在新worker运行时删表或回退到会超额的旧实现。

下一切片建议：解决人工请求的状态幂等、操作审计与参数归属（OPT-10、AC-10-07），不顺带扩展真实客服连接。提交后停止等待反馈。
