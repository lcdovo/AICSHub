# M1-02：工单与转人工创建的幂等隔离

- 日期：2026-11-03；基线：`47cc8172179568f61ed157d95bbdf37788671764`。
- 对应 **OPT-04 / OPT-10，E04；AC-04-03、AC-04-04**。附带覆盖AC-04-01/02的创建归属与管理员actor审计子集、AC-02-06的工单表单请求身份。
- 结论：本切片T0回归通过；真实MySQL/双worker、浏览器及故障恢复未执行，相关完整AC仍为部分验收，M1整体未通过。

## 修复的问题与行为

原工单及转人工POST入口均按全局原始键返回记录：B创建后A使用相同键会收到B的资源。原审计表也使用全局唯一键，跨操作重用键会触发数据库错误；相同键修改参数仍返回旧成功；并发先查后写存在唯一约束竞争错误。

新增`app/services/ticket_creation.py`，只承接这两个HTTP创建入口：

1. 幂等作用域包含可信actor、owner、operation、客户端key，生成固定长度内部键，使用现有审计表唯一约束，无进程锁/成功缓存。
2. 保存版本化业务参数与SHA256请求摘要。补全schema默认值后比较，JSON对象键顺序稳定；同键改参数409，相同请求读取原资源ID与当前状态。
3. 审计、资源、待发送通知同事务提交；唯一键竞争失败后先rollback再读取胜者。捕获actor ID避免rollback使认证ORM对象过期后出现隐式异步读取错误。
4. 保留普通用户owner限制；管理员代建记录actor和owner；新建记录校验关联会话归属和目标用户存在。
5. 请求头/体key不一致、空白或超长key返回400。未提供key仍是独立创建。没有更改优先级、SLA、分配与解决规则。
6. 旧版原始key缺可信actor和请求摘要，返回409并保留历史记录，不猜测历史授权、改写数据或重执行。回执指向删除资源时也冲突，不复活资源。
7. 工单前端同参数失败重试沿用key，改参数生成新key；DOM、文案、样式均未修改。改参数的新请求仍需用户核查未知结果，不能据此保证跨请求业务意图去重。

契约已同步`docs/API.md`。无需schema迁移，前提是既有`operation_audits.idempotency_key`数据库唯一约束存在；模型及原Alembic空库初始化包含该约束。本轮只在临时SQLite启用外键并建表验证，未核验个人业务库的实际schema，未运行主库迁移。

## 回归与可复现证据

新文件`tests/test_ticket_idempotency.py`共30项，使用真实ASGI传输、独立文件SQLite与每请求SQLAlchemy会话；认证为合成header替身，但主体从同一数据库会话读取，覆盖rollback对认证对象的影响。没有真实JWT/供应商/支付/通知调用。

- 新增首批17项在修复前 **14 FAIL / 3 PASS**，稳定复现A收到B资源、改参数返回旧成功、跨操作键审计冲突、会话归属和键冲突等问题。
- 最终30项全部通过：跨用户/操作人/操作类型隔离、7种参数变更、原参数重放、当前状态变化、owner/session越权、键校验、管理员审计、旧键安全拒绝、提交前故障回滚与并发。
- 并发每种操作同输入20轮，每轮10请求；用barrier确保全部先观察到无回执，再争取唯一键。所有响应成功且ID相同；逐轮核对资源、通知、审计各只增1条。另各1轮不同参数混合竞争，赢家参数组成功，其他409，记录仍只有1份。一次套件共420个并发请求；不把它们计成420个独立pytest用例。
- 提交前故障：flush后、commit前抛出异常，三个表均无残留；同键随后可正常创建。没有模拟提交后真实进程崩溃/断网，不冒充完整恢复验收。

| 执行 | 实际结果 | 原始证据（本机，Git忽略） |
| --- | --- | --- |
| 修复前首批新用例 | 14 FAIL / 3 PASS，退出1 | `reports/offline/20261103-131113-446085/` |
| `python scripts/test_offline.py -q --tb=short tests/test_ticket_idempotency.py tests/test_chat_turns.py tests/test_refund_dependency.py tests/test_refund_flow.py` | **84 PASS**，退出0，网络拦截数0 | `reports/offline/20261103-131403-245376/` |
| `python scripts/test_offline.py -q --tb=short tests` | **205 PASS / 2 FAIL**，退出1；无skip/xfail | `reports/offline/20261103-131522-496591/` |
| `frontend: npm test -- --reporter=dot` | 5文件、**22 PASS**，退出0 | `reports/offline/m1-02-frontend-test.log` |
| `frontend: npm run build -- --outDir ../reports/offline/m1-02-frontend-build` | TypeScript/Vite通过，退出0 | `reports/offline/m1-02-frontend-build.log` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-02-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

这些集合相互重叠，不叠加。全量两项失败与M0/M1-01完全相同：

- `test_intent.py::test_low_confidence_keeps_llm_fallback`：单字“退”是否调用模型的策略断言冲突。
- `test_query_rewrite.py::test_no_previous_context_calls_llm`：无上文simple短路与预期模型调用次数冲突。

没有改动这两项断言、业务策略或验收标准。仍有既有asyncio scope、UTC时间和multipart弃用警告。

manifest中的commit为父提交，实测时存在本切片工作区变更。实测源码SHA256（工作区字节，Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/routers/tickets.py` | `FD1C0988BF0940F78391F41ED3515B1DEB2259F74D06C4D7A74E1C2E4856353A` |
| `app/services/ticket_creation.py` | `16C37EABB8B320FD8C708151235383BF184AC4D64C05F0F9D84C33549E6B452C` |
| `tests/test_ticket_idempotency.py` | `4AA88D31F81CE3543C336F252A130A5E4B3C96A35082A0F15D01D5E2BD1C0C60` |
| `frontend/src/features/tickets/TicketForms.tsx` | `2C49D18A187C5F8F3924B39785C3B6E2152B9700413A56F857C117321114064E` |
| `frontend/tests/forms.test.tsx` | `D1AA7BC0AB0EFD5ECA221DD2C3118D7246260C649F536CEA4541C432B1C7C380` |

## 限制、资源保护与后续

- 未验证真实MySQL唯一约束/隔离级别、双worker、持久进程重启、真实JWT、浏览器完整流程、真实模型、性能、部署与回滚。SQLite并发成功不代表MySQL生产承诺。
- 聊天工具和内存协同仍未接入本服务（E01/E03），BaseTool缓存及MCP未知结果重试仍待后续；本次保证仅覆盖两个HTTP创建入口。通知入队不代表发送，人工请求创建不代表座席接单。
- 不能以回滚到原越权实现作为安全回退；未来升级应完整切换写入worker，先验证真实schema/历史键；不要新旧写协议混跑。本轮没有部署或切换服务。
- 原三个未跟踪文件保留；未读取项目敏感配置、清空业务库/知识库/数据卷、停止容器、推送或产生外部费用。构建只写忽略目录，保留原static输出。
- 建议下一切片修复会话创建的owner与Redis快照schema（E06/E07，OPT-03/05，AC-03-02/03），与WS授权等后续工作分开实施。本轮提交后停止等待反馈。
