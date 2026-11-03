# M1-04：REST / WS 身份撤销与每轮授权

- 日期：2026-11-03；基线：`e8b9cf6d2dfa09deb57dc736b8272a2801a8595c`。
- 对应 **OPT-04、E05、AC-04-05/06/07**。覆盖注销/版本撤销、禁用/删除、到期/签名/用途、数据库角色及顺序refresh轮换；不包含并发刷新原子性、WS票据与运行中动作撤销。
- 结论：本切片T0检查通过，完整真实依赖与生产部署仍未验收，M1整体未通过。

## 问题与修复

原WS握手仅解码JWT并读取sub，之后每轮只检查用户存在，没有对齐REST的status、disabled_at和token_version；已连接Token到期/注销后仍可执行下一轮。原JWT校验不强制exp，布尔ver可与整数0比较相等。

本轮还确认新缺陷E27：注销递增token_version后，若请求未携带某个已发行refresh，原刷新入口不比较版本，仍能换取新access，绕过版本撤销。

1. `app/security.py`提供共享`authenticate_access_token` / `user_for_token`。Token要求exp/sub/type/ver完整，sub为正整数ID字符串、ver为非负整数，拒绝布尔版本；保持HS256签名、到期与用途校验。数据库查询使用populate_existing，检查当前账号状态及版本，role来自数据库。
2. REST依赖调用共享身份校验；WS握手、逐消息、取得会话锁后均校验。REST聊天等待锁后也重新校验Bearer身份与owner权限，避免排队期间注销或降级仍沿用旧授权。身份读取不使用先前读事务快照，现有会话锁/租约保持不变。
3. WS握手身份失败关闭4401；已连身份失败先报401再关4401；身份正常的越权业务请求报403，可继续合法本人请求。WS仍不允许管理员代其他用户聊天，未扩大权限。
4. Refresh入口在原有摘要、有效期、revoked检查之外复用当前账号/版本校验。注销的版本递增同时失效此前所有access与refresh，disabled_at不再被刷新路径绕过；保留顺序轮换。
5. WS Bearer scheme大小写与HTTP一致。非业务异常的响应不透传异常文本，错误日志只记录异常类名。认证数据库故障不回退示例身份；握手/逐消息阶段的SQLAlchemy异常关闭1013（已连先报503），执行阶段仍按脱敏500处理，此故障分支本轮未做真实依赖故障验收。

没有修改数据库schema/数据、Token传递协议、会话恢复或业务动作规则。正常客户端已由本项目签发的Token包含这些必填字段；缺字段的历史/自定义Token将被拒绝，需重新登录。API与安全文档已同步。

## 验证与证据

`tests/test_chat_auth.py`最终25项：真实JWT签发/验签、REST与WS处理器、独立临时SQLite；没有覆盖get_current_user，只有图执行使用固定合成回复替身，Redis关闭。禁用/删除/版本变更由独立数据库会话施加；锁等待期间变更用确定性barrier替身。删除认证记录用临时SQLite直接删除保留合成会话，验证缺主体拒绝，不代表真实数据库硬删除/外键流程。

覆盖新连接和旧连接的版本撤销、status/disabled_at、账号删除、注销、到期；错误签名、refresh当access、缺exp/ver、错误sub、布尔ver；DB管理员降级；REST/WS等待锁期间撤销；管理员等待锁期间降级后他人资源拒绝；合法Token/本人请求及越权帧后继续；Refresh注销版本、disabled_at与顺序轮换后旧Token拒绝。拒绝路径断言不新增消息、不运行图；已完成首轮的撤销用例保持原两条消息。

| 执行 | 结果 | 原始证据（Git忽略） |
| --- | --- | --- |
| 修复前首批19项 | 14 FAIL / 5 PASS，退出1。其中12项为实际契约失败，2项为删除夹具触发ORM置空sessions.user_id约束，不能算鉴权缺陷证据 | `reports/offline/20261103-132319-332541/` |
| 首次修复后19项+原聊天/会话集 | 82 PASS / 2 FAIL，均为上述删除夹具问题；已修正夹具直接删除合成主记录，未改应用删除逻辑 | `reports/offline/20261103-132402-832960/` |
| `python scripts/test_offline.py -q --tb=short tests/test_chat_auth.py tests/test_chat_turns.py tests/test_session_creation.py`（增加最后两项前） | **88 PASS**，退出0 | `reports/offline/20261103-132521-338658/` |
| `python scripts/test_offline.py -q --tb=short tests`（最终源码/测试） | **257 PASS / 2 FAIL**，退出1；新认证25项全部PASS，0 skip/xfail，网络拦截数0 | `reports/offline/20261103-132553-598627/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-04-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

集合互相重叠，不累加。最终两项失败与M0一致：`test_intent.py::test_low_confidence_keeps_llm_fallback`（单字“退”的策略断言冲突）、`test_query_rewrite.py::test_no_previous_context_calls_llm`（无上文simple短路与模型次数冲突）。未改这些断言或业务策略。保留既有UTC、asyncio scope和multipart弃用警告。没有前端变更，本轮未重复前端测试/构建或浏览器验收。

manifest为父提交及执行环境记录，实测时有工作区变更；实测源码SHA256（Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/security.py` | `282326042556C96F6E9210C6F23F9EEC68BB8059739FE2E09D892E8F99B9E9E8` |
| `app/routers/chat.py` | `2EEF92ECB0172C3D15A3DBE08CBC34CBA7728DD1B51D374B7F8DE83FB3E5C339` |
| `app/routers/users.py` | `78D0434741A4B13676B0EA0020177D3EDC8D5404E788DE21FEAC6447661F8FAD` |
| `tests/test_chat_auth.py` | `0E4813394DCDBD94D9756DB4EBD3940D8B00E39614553559993F8484E644EEC4` |

## 范围限制与后续

- 未验证真实MySQL隔离级别、多worker、Redis/数据库故障、反向代理、浏览器、真实模型、部署/回滚、性能/成本或完整恢复。顺序刷新不能证明并发刷新只有一个胜者。
- 撤销发生在本轮最后一次校验之后、图或工具已开始时，不能据此保证即时取消/停止已提交副作用。WS空闲连接在下一条消息才发现到期/撤销，没有主动踢线轮询。
- REST无session_id时可能在等待锁前已建空会话；等待期间撤销会阻断消息和图，但不宣称整次请求零数据库写入。旧关闭会话在WS自动重开的AC-03-05缺陷仍存在，未顺带修改规则。
- 保留WS查询参数Token兼容，未实现AC-04-08票据与代理日志屏蔽，不能宣称完整日志链无Token；本轮只在应用级采用静态脱敏错误，不发送真实Token或用户资料到外部。
- 业务库/知识库/数据卷未修改；无真实外部调用费用，无部署或推送；原三个未跟踪文件保留。
- 下一切片建议：统一REST/WS关闭会话拒绝行为（OPT-03、AC-03-05），覆盖锁等待期间关闭，避免WS自动重开；独立提交后继续停下等待反馈。
