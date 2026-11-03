# M1-05：已关闭会话的REST / WS准入一致性

- 日期：2026-11-03；基线：`1bd88c7f41932eda42e1a7f602a308bb41207068`。
- 对应 **OPT-03、AC-03-05**；附带覆盖AC-04-01的他人关闭会话权限子集。
- 结论：本切片T0检查通过；真实依赖/多worker与完整生命周期竞态未验收，不表示M1整体通过。

## 修复与行为影响

原REST对已关闭会话返回400；WS取得锁后却将状态设回ACTIVE并清空closed_at，用户再次发消息会自动重开。另发现REST先检查关闭状态再检查资源归属，他人关闭会话会返回400而非403，暴露其状态。

本切片仅改聊天路由两处：

- WS在锁内刷新数据库记录后，关闭状态直接抛出HTTPException(400, 会话已关闭)，使用已有WS错误映射；不改变status/closed_at，不运行图、不发送生成事件。保留连接，可在另一个新建且授权的会话继续聊天。
- REST将资源归属检查移到关闭检查之前，与WS的权限优先顺序一致。他人会话返回403，不暴露是否关闭。

两种关闭入口原行为、REST的400状态码、共享会话锁/租约和每轮认证不变。没有新增重开接口、业务规则、schema迁移或删除逻辑。API说明已同步。

## 验证证据

新文件`tests/test_session_closed.py`共11项，复用真实JWT/ASGI认证夹具、独立临时SQLite；图执行固定合成替身，Redis关闭，不使用真实模型。

- 2个关闭入口 × REST/WS：关闭后重复发送两次，均400；数据库status、closed_at、消息计数、消息/Trace/评测数量不变，不调用图。最终测试额外保存合成历史及待确认快照，拒绝后对象不变。
- 2个关闭入口 × REST/WS：在聊天准入读取完成、取得本地会话锁前，通过实际ASGI关闭API提交关闭，再取得原本地锁；刷新后拒绝，零消息/Trace/评测写入，锁注册表释放。
- REST/WS请求他人关闭会话均403，状态不变。
- 同一个WS连接先收到关闭错误，再对新建本人会话完成正常聊天；旧会话不重开。

| 执行 | 实际结果 | 原始证据（本机Git忽略） |
| --- | --- | --- |
| 修复前11项 | **6 FAIL / 5 PASS**，退出1；WS自动重开4项、REST权限顺序1项、WS错误后继续新会话1项失败 | `reports/offline/20261103-132852-403736/` |
| `python scripts/test_offline.py -q --tb=short tests/test_session_closed.py tests/test_chat_turns.py tests/test_chat_auth.py tests/test_session_creation.py`（最终快照不变断言添加前） | **101 PASS**，退出0 | `reports/offline/20261103-132913-651628/` |
| `python scripts/test_offline.py -q --tb=short tests`（最终源码/测试） | **268 PASS / 2 FAIL**，退出1；新增11项全部PASS，0 skip/xfail；网络拦截数0 | `reports/offline/20261103-133002-199166/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-05-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

集合重叠，不累加。两项全量失败与M0一致：`test_intent.py::test_low_confidence_keeps_llm_fallback`（单字“退”的策略断言冲突）和`test_query_rewrite.py::test_no_previous_context_calls_llm`（无上文改写短路与预期模型次数冲突）；未改断言、策略或验收标准。保留原asyncio scope、UTC与multipart弃用警告。无前端修改，本轮没有重复前端测试/构建或浏览器检查。

manifest记录父提交和环境，实测时存在本切片工作区变更；实测工作区SHA256（Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/routers/chat.py` | `F168FD9D69ED8E88E24513C1CF4FB474755AA3EA7298BD67FEA75301CA50865D` |
| `tests/test_session_closed.py` | `74B4862C1385244FC3A06BC05711467D58348221335DB518781887AE128BF40B` |

## 边界与后续

关闭入口本轮仍没有与聊天共用锁；最后一次状态检查之后才发生关闭，已开始的轮次可能继续完成。本次不覆盖运行中取消、关闭/删除竞态的CAS/tombstone、真实多worker、MySQL隔离级别、Redis故障、模型/工具中途停止、部署回滚或完整恢复（AC-15-10仍未通过）。没有将确定性等待窗口测试表述为完整并发验收。

仅使用合成数据；个人业务库、知识库、数据卷、static输出与原三个未跟踪文件保留；无真实外部费用、部署或推送。

下一切片建议回到P0假成功：让聊天创建工单调用已有持久创建服务并返回可查记录（OPT-08/10，AC-08-01、AC-10-01），独立处理可信身份与回执契约。本切片提交后停止等待反馈。
