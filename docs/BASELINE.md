# 第 1 步基线与本地运行

本项目的本地 profile 使用 SQLite、内存检索和 Mock LLM，不需要预先运行 MySQL、Redis、Milvus 或配置模型密钥。该模式用于开发演示和回归测试，不代表生产高可用能力。

## 启动

PowerShell 执行：

```powershell
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
./scripts/dev.ps1
```

服务启动后可访问：

- `http://localhost:8000/health`：依赖状态、降级原因和模式
- `http://localhost:8000/docs`：OpenAPI 文档

集成 profile 需要自行提供 MySQL、Redis、Milvus 和 LLM/Embedding 服务，并通过环境变量覆盖本地配置。

## 验证

```powershell
python -m compileall app scripts
python -m pytest -q
```

当前基线回归集覆盖健康检查、OpenAPI、聊天 API smoke test 以及最小 Mock Agent 流程。测试使用独立 SQLite 文件，不依赖外部服务。

## 状态约定

`/health` 将数据库视为必需依赖；Redis、Milvus 和 LLM 为可选依赖。未配置或不可用时会返回 `degraded`/`disabled` 及原因，便于启动检查和故障演示。
