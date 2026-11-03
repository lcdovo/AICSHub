# M1-01：数据库故障时阻断示例退款成功

- 日期：2026-11-03。
- 基线提交：`db57170`（M0），应用原始基线 `2c2e5b0`。
- 对应：**OPT-08 / OPT-09，E02，AC-09-02、AC-08-02 的退款分支**。
- 结论：本切片 T0 契约回归通过；AC 的完整真实集成仍 NOT_RUN，M1整体未通过。

## 行为变化与边界

`ApplyRefundTool.execute` 原先捕获数据库异常后，用两个固定示例订单号直接生成299元待审核回执，无法验证用户归属和资格。现在工厂创建、上下文进入、查询或退出任一异常均立即返回脱敏失败：`success=false / error_type=dependency_unavailable / retryable=false`。

不生成退款ID、金额或待审核数据。`execute_with_retry` 保留失败类型，不自动重试或缓存该结果。错误提示明确“未提交退换货申请”。没有新增数据库表、迁移、配置或外部调用。

正常数据库查询成功后的退款申请仍未持久化（E01），并非真实退款闭环。本轮未改变其业务规则，后续须使用真实申请记录、确认action与持久幂等替代虚假回执。工单幂等越权E04、会话schema E06、WS授权E05等P0仍未修复。

## 回归证据

新增 `tests/test_refund_dependency.py` 共13例：

- 3个订单号 × 4个数据库故障点 = 12项替身故障；包括两个旧示例号和普通号。断言失败分类、无数据/ID、不透传合成连接错误、不自动重试、不缓存。
- 1项真实临时SQLite负向查询：合成B订单不能被A申请，不存在订单不能申请，原订单所有者不变。SQLite不代表MySQL集成。

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 修复前新增退款集 + M0探针 | 新退款12 FAIL/1 PASS；E04/E06另2 FAIL | `reports/offline/20261103-130707-468332/` |
| `python scripts/test_offline.py -q tests/test_refund_dependency.py tests/test_refund_flow.py tests/test_chat_turns.py tests/test_tool_retry.py tests/test_graph_routing.py` | **82 PASS**，退出0，网络操作拦截数0 | `reports/offline/20261103-130821-890599/` |
| `python scripts/test_offline.py -q --tb=short tests` | **175 PASS / 2 FAIL**，退出1；仅M0原有两项失败，0 skip/xfail | `reports/offline/20261103-130854-544278/` |
| `python -m compileall app scripts` | PASS，退出0 | `reports/offline/compileall.log` |
| `git diff --check` | PASS | 提交前检查 |

82是全量177项的子集，不相加。全量失败名和原因见 [M0基线](OPT-M0-baseline.md)，没有修改断言或降低验收标准。另一次仅传flags而未传tests路径的执行收集0项，退出非0（`20261103-130841-934050`），不计通过；当前入口带flags时需显式传测试路径。

运行时 manifest 的commit指M0父提交，应用及新测试当时尚未提交。实测源码SHA256（提交前字节；Git可能规范化行尾）：

| 文件 | SHA256 |
| --- | --- |
| `app/agent/tools.py` | `E973D1BADE66BA48B2E0DE71435F330A557BF1F1F687EAC0FA6659DDD5C79540` |
| `tests/test_refund_dependency.py` | `3F42F9D1B722C08528E8BB74EC177C16A10C931E51FA371DF92BFDFFF3F00836` |
| `scripts/test_offline.py` | `D1F27BD608731E544A50AE3B9298469DB4FAD1D5C5138037D19814E89C97ACB5` |

本次无前端代码变更；M0的21项前端测试及独立输出构建通过，见基线。未运行浏览器验收、真实MySQL/Redis/Milvus、真实模型、真实MCP stdio、崩溃恢复、迁移/备份恢复、性能或完整部署；没有产生真实外部调用费用。

## 文件与后续

本切片仅提交 `app/agent/tools.py`、`tests/test_refund_dependency.py`、本报告。原有三个未跟踪文件保留，不推送。

建议下一切片处理工单/转人工幂等的资源归属与冲突，优先AC-04-03，并覆盖AC-04-04的同主体并发和改参数冲突；本轮完成后停下等待反馈。
