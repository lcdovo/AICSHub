# 评测与可观测性闭环

第 6 步将一次请求的 `X-Trace-Id` 作为请求级 Trace ID，节点记录使用独立 Span ID，并同时写入 `agent_traces`。普通 HTTP 与 WebSocket 均把评测结果写入 `evaluation_results`，记录评测器、模型/Prompt 版本、Token、耗时和原始问答（生产环境应在日志侧脱敏）。低分结果自动进入内存低分池，并保留数据库状态用于后台处理。

管理员接口：

- `GET /api/v1/analytics/traces/{trace_id}`：查询一次请求的节点 Span。
- `GET /api/v1/analytics/metrics`：JSON 指标和告警。
- `GET /api/v1/analytics/metrics/prometheus` 或 `GET /metrics`：无敏感标签的 Prometheus 文本。
- `GET /api/v1/analytics/evaluation/low-scores`、`POST .../{sample_id}/resolve`：低分样本处理闭环。

核心指标包括请求成功率、P50/P95 响应延迟、工具成功率、RAG 空召回/降级率、模型失败率与 Token 数、人工转接率、CSAT 和低分样本数。当前评测器为可复现的规则评测；LLM-as-a-Judge 和人工抽检可在后续替换/叠加，不影响数据契约。
