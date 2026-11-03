# M1-08：未接入通知渠道时明确失败

- 日期：2026-11-03；基线：`c1f5e9fdebe690c02eccd5f7d6ae9ca78dd1a971`。
- 对应 **OPT-08、E01的通知分支、AC-08-02**；附带覆盖本工具的严格参数、重试及MCP一致性子集。
- 结论：本切片T0与同进程MCP检查通过；没有实现通知发送能力，M1整体仍未通过。

## 现状核实与修复

源码中没有短信、邮件或站内信投递适配器，也没有消费通知队列的worker或相关发送配置。`NotificationRecord`和`OutboxRepository`只提供待发送事件与状态写入方法，不能证明真实或模拟投递。原模型注释“由Mock worker消费”不符合当前实现。

原`SendNotificationTool`没有调用投递服务或落库，却返回随机notification_id、status=sent、sent_at与“通知已发送”。本轮：

1. sms/email/inapp合法请求统一返回`success=false / error_type=channel_unavailable / retryable=false`。没有data、ID、sent状态或sent_at，不写库、不伪装入队为发送成功。
2. 设置`cache_success=False`、`max_retries=1`，阻止取用旧假成功缓存，不对明确不可用自动重试。没有清理现有业务记录或缓存数据。
3. 通知输入使用严格schema：拒绝布尔/字符串user_id、非法类型/渠道、空白内容和未知字段；内容长度5至10000字符。保留原MCP省略user_id默认0的合法调用格式，但不会授权或执行发送。错误不回显通知正文或个人资料。
4. 直调工具与MCP说明均标记发送能力未接入。修正NotificationRecord模型注释并同步API/图说明，保留数据表结构与状态枚举。

已有工单/转人工事务写入的NotificationRecord(PENDING)不变，只表示业务事件待处理。没有新建消费worker、接入模拟或真实渠道、增加配置或外部服务费用。Outbox状态写入助手保留，不把助手本身视为投递证据，也没有回溯改写历史通知状态。

## 回归与证据

新文件`tests/test_notification_unavailable.py`共20项：3种渠道的无回执/无ID/不访问数据库；3种渠道绕过旧假成功缓存且只调用一次；8种非法输入；真实临时SQLite证明已有pending事件及审计不受影响；3种渠道真实SDK同进程MCP及工具说明；2种注册表路径（直调、MCP不可用回退）。无真实外部渠道调用。

| 执行 | 实际结果 | 原始证据（本机Git忽略） |
| --- | --- | --- |
| 修复前首批15项 | **15 FAIL**，退出1；包括合法调用/旧缓存返回假sent、非法输入未拒绝或缺少错误分类、部分类型触发异常 | `reports/offline/20261103-135225-502357/` |
| `python scripts/test_offline.py -q --tb=short tests/test_notification_unavailable.py tests/test_tool_retry.py tests/test_ticket_idempotency.py tests/test_ticket_tool.py tests/test_ticket_update_tool.py` | **115 PASS**，退出0 | `reports/offline/20261103-135344-417599/` |
| `python scripts/test_offline.py -q --tb=short tests` | **346 PASS / 2 FAIL**，退出1；新增20项全部PASS，0 skip/xfail，网络拦截数0 | `reports/offline/20261103-135521-957560/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/m1-08-compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

集合重叠，不累加。两项既有失败仍为`test_intent.py::test_low_confidence_keeps_llm_fallback`和`test_query_rewrite.py::test_no_previous_context_calls_llm`，策略断言冲突原因见M0；未改这些断言、策略或验收标准。保留已有UTC、asyncio scope、multipart和SDK依赖弃用警告。

manifest记录父提交及环境，实测时有本切片工作区变更。实测工作区SHA256（Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/agent/tools.py` | `8CBF1B64C7B49E53B70DBE41BA6E0D18EC432035811E3D7386D63F497EE7AD6A` |
| `app/agent/mcp_server.py` | `6A134D1DDBBCEC74D18F5C5E0F7EDA3277C4CE902D77C06B5F1C5F915D215C56` |
| `app/models/models.py` | `B544637529DE37BFFD40EF6AEA1A152562B91E5A1BD4667DF68268E1A623646D` |
| `tests/test_notification_unavailable.py` | `F9C657AC9C4A4F4A33AA228AEDD5E157708636E2F1C2E6CEC4C19F7805B7B4D0` |

## 修改文件与限制

- 工具/协议：`app/agent/tools.py`、`app/agent/mcp_server.py`。
- 模型说明：`app/models/models.py`（仅注释，无DDL变化）。
- 测试：`tests/test_notification_unavailable.py`。
- 文档：`docs/API.md`、`docs/AGENT_GRAPH.md`、本报告。

这是阻断假成功，不是发送集成验收。真实渠道、worker、真实MySQL/Redis、stdio、浏览器、并发投递/租约/重试/对账、部署与回滚未验证。未来启用通知需要独立完成可信身份、持久幂等、投递回执与恢复契约，并确认真实费用；不能以改配置或把入队标成sent代替验收。

没有前端修改，本轮未重复构建/浏览器验收；业务数据库、知识库、数据卷、static及原三个未跟踪文件保留；无真实外部费用、部署或推送。退款与转人工仍有剩余假成功，不宣称E01整体关闭。

下一切片建议：转人工工具移除虚构队列/等待回执并统一持久请求（OPT-08/10、AC-08-01的人工分支），独立核实其状态与回执。提交后停止等待反馈。
