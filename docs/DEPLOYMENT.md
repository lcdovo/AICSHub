# Docker 部署

## M1-13 模拟退换货申请升级补充

新增`0006_refund_applications`只建立模拟待审申请表，不修改订单、不回填旧伪回执、不执行支付。未来切换前需迁移并完整切换工具worker；缺少内部确认凭据的旧调用将失败。此轮只在独立SQLite演练新增表upgrade/downgrade/upgrade，未对业务库运行迁移或部署。真实MySQL与备份恢复未验；不能在新worker运行时删除申请表，也不应回退到会生成假成功的旧工具。

## M1-11 人工分配升级补充

新增迁移`0005_handoff_capacity`只建立`handoff_capacity_gates`事务闸门表，不修改历史人工指派或种入演示座席。切换分配写流量前需迁移并确保所有worker使用新分配路径及一致的名册/容量配置；旧版本直接写ASSIGNED可绕过闸门，不应混跑。此轮仅在临时SQLite演练新增迁移的upgrade/downgrade/upgrade，没有在当前业务库迁移或部署。

真实MySQL行锁、双worker、部署与备份恢复尚未验收。降级应先关闭分配写入；旧实现可能超额分配，不作为安全回滚目标，也不能在新worker运行时删除闸门表。此补充不表示下文历史部署说明已完整核验，实际Compose与启动参数以当前配置为准。

项目唯一支持的 Compose 部署是完整依赖栈：应用、MySQL、Redis、Milvus、etcd 和 MinIO。所有服务都在 [docker-compose.yml](<C:/Users/lz/Desktop/简历&项目/项目一/AI Customer/docker-compose.yml>) 中定义，不使用 Docker profiles。

## 首次部署

前端生产资源在 Docker 的 Node 构建阶段从 frontend/ 源码生成，复制到容器内 /app/static/；不再依赖 Git 中预存的静态构建文件。本地可运行 cd frontend 后执行 npm ci、npm test、npm run build；Vite 输出仍为根目录 static/，资源路径 /static/。部署前应先在隔离环境检查页面，不要用前端构建替代 API / 模型验收。

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up -d --build
docker compose ps
```

`.env` 必须提供非空的 `MYSQL_ROOT_PASSWORD`、`MYSQL_APP_PASSWORD`、`MINIO_ROOT_PASSWORD` 和至少 32 字符的 `AUTH_SECRET_KEY`。真实 LLM/Embedding 服务还需要填写对应的 API 地址、密钥和模型名称。

首次启动依赖健康后，在应用容器中执行 Alembic 迁移：

```powershell
docker compose run --rm app alembic upgrade head
```

迁移完成后访问 `/health`；应用和所有依赖正常时，数据库、Redis 和 Milvus 应显示为 healthy。Compose 已配置健康检查、依赖启动顺序和持久化卷。

## 日常操作

```powershell
docker compose up -d                 # 启动或恢复全部服务
docker compose logs -f app           # 查看应用日志
docker compose restart app           # 仅重启应用
docker compose pull                  # 拉取新基础镜像
docker compose up -d --build app     # 发布新的应用代码
docker compose down                  # 停止服务，保留数据卷
```

数据卷包括 `mysql_data`、`redis_data`、`etcd_data`、`minio_data` 和 `milvus_data`。`docker compose down -v` 会删除它们，执行前必须确认已经完成备份。

## 安全上线清单

1. 使用随机且至少 32 字符的 `AUTH_SECRET_KEY`，不要使用示例值。
2. 保持 `DEBUG=false` 和 `AUTH_DEMO_ACCOUNTS_ENABLED=false`。
3. 设置明确的 `CORS_ALLOW_ORIGINS`，禁止生产环境使用 `*` 配合 credentials。
4. 通过密钥管理系统注入 MySQL、MinIO、LLM 和 Embedding 凭据，不要写入镜像或日志。
5. 首次上线先执行 `alembic upgrade head`，确认迁移成功后再接入流量。
6. 为 MySQL、Milvus、MinIO 数据卷制定备份和恢复方案。

## 健康检查与故障

- `GET /health` 返回应用、数据库、Redis、Milvus 和 LLM 状态及降级原因。
- `GET /docs` 提供 OpenAPI 文档。
- 如果 MySQL、Redis 或 Milvus 尚未 healthy，应用容器会等待依赖；使用 `docker compose ps` 和 `docker compose logs <service>` 定位问题。
- LLM 或 Embedding 不可用时，应用会记录可解释的降级信息；生产环境仍应配置外部服务的超时、重试和监控。
