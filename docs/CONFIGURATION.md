# 配置来源与当前运行快照

## 配置来源

应用字段、默认值和校验以 `app/config/config.py` 的 `Settings` 为准。Compose 主栈将配置通过 `app-environment` 显式传给 API，并由 Worker 继承同一组环境变量；只在 `.env` 中写入、但 Compose 没有映射的变量不会自动进入容器。

`.env.example` 是保守起步模板，不是当前开发机的镜像。它默认使用 rules 请求理解、Judge v1、JSON 外的原有结构化协议、单 worker 和本地重排 batch 4。复制后需要按自己的验收状态配置；不能据此推断其他主机实际启用了哪些功能。

Compose 的插值默认值应与 `Settings` 默认值一致。若希望开发机配置影响容器，必须同时在 Compose 的 `app-environment` 映射该字段。`WORKERS` 映射自 `APP_WORKERS`，供应用内启动器读取；Compose 的 Uvicorn 命令也使用同一个 `APP_WORKERS` 值。数据库地址由 Compose 从 MySQL 服务变量生成；不要通过 `DATABASE_URL_OVERRIDE` 绕过部署连接配置。

新增 Settings 字段时，应同时检查 Compose 映射、模板值及 `tests/test_compose_configuration.py`。密钥只保存在本地 `.env` 或密钥管理服务中，不写入本文、截图或版本控制。

用户历史召回新增`USER_MEMORY_MIN_SIMILARITY`（默认0.65，范围0至1），API/Worker共用Compose映射。向量分数低于门槛时允许空召回，Embedding故障后的关键词回退要求有效匹配。该值为可配置初值，未代表当前模型已完成语义校准；详见[上下文与记忆优化](MEMORY-CONTEXT-OPTIMIZATION-20261111.md)。

共享上下文预算集中在`app/agent/context_budget.py`，以UTF-8字节控制背景组合规模，不修改供应商/独立Judge原有门槛。长期沟通偏好采集受`USER_MEMORY_ENABLED`控制，180天到期；本机已完成0014迁移并部署。实现边界见[记忆分层优化](MEMORY-LAYERS-OPTIMIZATION-20261111.md)，实际发布见[完整功能部署](FULL-FEATURE-ROLLOUT-20261111.md)。

## 本机运行快照

以下是 **2026-11-11** 在本机容器内读取的非敏感有效配置，不是模板默认值，也不代表其他环境：

| 项目 | 实际值 |
| --- | --- |
| API 与 Worker | 同一镜像 `732fa406e83b`；检查时均 healthy |
| 数据库迁移 | `0014_user_memory_entries` |
| 请求理解 | `model`，灰度 100% |
| 任务循环 / 多意图逐项 | 开启 / 开启 |
| SQL 模型网关 | 开启 |
| 独立 Judge | v2，`json_object` 协议 |
| 单轮超时 | 90 秒 |
| RAG 候选 / 最终重排 | recall 20 / rerank 12 / top 3 |
| RAG 上下文 | RRF 60，证据上限 8000 字符，邻块 1 |
| 本地重排 batch | 20 |
| RAG 向量相似度阈值 | 0.4 |
| 用户记忆 / 历史向量门槛 | 开启 / 0.65，尚未语义校准 |

检查依据为容器内 `Settings`、`docker inspect` 健康状态、`/health` 和实际SQL迁移版本。本次实现源码与运行镜像的核对及合成SQL/HTTP检查见完整功能部署记录；不代表完整语义准入或生产容量验收。历史的阶段性部署和模型验收边界仍按各自报告保留。
