# 5 分钟面试演示脚本

演示默认使用 `local` profile，不需要 API Key 或外部基础设施。开始前执行 `Copy-Item .env.example .env; ./scripts/dev.ps1`，或直接运行 `./scripts/demo.ps1`。

## 演示时间线

1. **登录（0:00–0:30）**：用 `user001 / user12345` 登录，展示 JWT、刷新 Token 和普通用户权限。
2. **知识库（0:30–1:20）**：切换管理员账号，在 Swagger 调用 `POST /api/v1/knowledge/documents/seed`，展示文档分块、发布状态和 `backend=memory/degraded=true` 的本地降级标记。
3. **RAG 问答（1:20–2:00）**：普通用户调用 `/api/v1/knowledge/rag-query`，展示回答、来源标题、检索策略和耗时。
4. **订单与退款确认（2:00–3:15）**：发送“查询订单 ORD20240001”，再发送退款意图；首次只返回退款预览，用户明确回复“确认”后才执行 Mock 工具，展示 Trace 中的 confirmation 节点和幂等键。
5. **转人工与后台（3:15–4:15）**：发送“转人工”，展示 `/api/v1/handoff` 记录；管理员在 `/api/v1/handoff/requests` 分配并解决，随后查看低分样本和 `/metrics`。
6. **指标与故障（4:15–5:00）**：打开 `/health` 和 `/docs`，说明请求 P95、工具成功率、RAG 空召回率；将 `USE_MILVUS=false` 或清空 `LLM_API_KEY`，演示可解释的内存/Mock 降级。

可执行版本会自动完成登录、种子文档、RAG、聊天、工单和转人工请求；每一步失败会打印接口响应，便于无外部服务时继续讲解。

## 故障演示

- LLM 超时：设置 `LLM_REQUEST_TIMEOUT_SECONDS=0.1`、`MOCK_LLM_ENABLED=false`，重启后聊天仍返回规则降级回复，`/health` 标记 `llm=degraded`。
- Milvus 不可用：设置 `USE_MILVUS=true` 但不启动 Milvus，`/health` 返回 `backend=memory` 与降级原因，RAG 不静默返回空结果。
- 工具失败：发送无效订单号，展示参数校验错误、重试/错误分类和人工兜底。
- 数据库不可用：停止 MySQL（integration profile），`/health` 返回 `status=unhealthy`，请求带 `X-Trace-Id` 便于定位；恢复后通过 Alembic 迁移继续启动。
