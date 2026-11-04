# M5 当前文件与证据清单

以下为本次M5新增/修改的主题文件；整体仍未完成。

| 主题 | 文件 |
| --- | --- |
| 冻结标准 | docs/M5-PLAN-v1.md、docs/M5-GATES-v1.json |
| 发布底线 | app/agent/publication_guard.py、app/agent/nodes/response_node.py、tests/test_m5_workflow_guard.py |
| Judge/证据 | app/agent/validation_llm.py、tests/test_m5_judge.py、tests/test_m4_generation_metadata.py |
| 固定事实 | app/agent/critical_facts.py、app/agent/validation.py、tests/test_m5_critical_facts.py、tests/test_chat_turns.py |
| 引用身份 | app/agent/citation_guard.py、tests/test_m5_citations.py |
| 数据/指标 | scripts/evaluate_m5.py、tests/test_m5_metrics.py、acceptance/m5/ |
| 本地消融 | scripts/evaluate_m5_local.py、app/services/knowledge_base/versioned_search.py、tests/test_m5_ablation.py |
| 过程证据 | docs/M5-EVIDENCE-v1.md、docs/AGENT_GRAPH.md |
| 待人工材料 | docs/M5-REVIEW-QUEUE-v1.md、docs/M5-HOLDOUT-*-v1.json、docs/M5-HOLDOUT-CORPUS-REVIEW-v1.md、docs/m5-review/batch-01～12.md |

原方案/验收规范和scripts/_dev_run_import.py继续保持原未跟踪状态；不夹带提交。reports下报告被Git忽略，本地路径由证据文档记录，不提交数据库/密钥/日志/构建产物。

最终验收汇总：docs/M5-ACCEPTANCE-v1.md；全局195项索引同步M5局部证据，未标整体通过。595候选为助手草拟，只通过程序完整性，待用户语义/事实复核。网关截止测试调整在tests/test_m4_gateway.py，退款恢复审查在app/services/run_execution.py及tests/test_m2_runs.py；均有独立提交和保留失败证据。

后续重排预算切片：修改app/services/reranker_service.py、app/services/knowledge_base/versioned_search.py、scripts/evaluate_m5_local.py、tests/test_reranker.py；新增tests/test_m5_rerank_budget.py、docs/M5-RERANK-BUDGET-v1.md，更新验收证据/门禁/本文件及全局JSON索引。功能107项通过，实际5秒性能8/12local、4超时回退，仍未验收通过。

资源对照切片：scripts/evaluate_m5_local.py隔离loopback端点；tests/test_m5_local_endpoint.py九项边界测试；更新M5设备对照、证据、验收门禁和本清单。业务生产代码与部署未修改。
