# 5 分钟演示脚本

演示使用项目唯一的完整 Docker 部署。开始前准备 `.env`，然后执行：

```powershell
docker compose up -d --build
docker compose run --rm app alembic upgrade head
```

确认 `http://localhost:8000/health` 正常后，打开 `http://localhost:8000/docs`。如果系统尚未初始化账号，请先完成管理员初始化，再进行以下流程：

1. 登录并展示 JWT、刷新 Token 和普通用户权限。
2. 使用管理员接口创建或发布知识库文档。
3. 使用 `/api/v1/knowledge/rag-query` 展示 BM25、向量检索和重排结果。
4. 发送订单查询和退款意图，展示确认节点、幂等处理和工单状态流转。
5. 发起转人工请求，使用管理员接口查看、分配并解决请求。
6. 查看 `/health` 与 `/metrics`，说明请求延迟、工具成功率、RAG 和依赖状态。

## 故障演示

- LLM 超时：调整 `LLM_REQUEST_TIMEOUT_SECONDS`，确认规则路由和可解释降级仍然生效。
- Milvus 不可用：停止 `milvus` 容器，`/health` 应报告降级原因；恢复后重新检查健康状态。
- 工具失败：发送无效订单号，展示参数校验、错误分类和人工兜底。
- 数据库不可用：停止 `mysql` 容器，`/health` 应返回 unhealthy；恢复后执行必要的 Alembic 迁移。
