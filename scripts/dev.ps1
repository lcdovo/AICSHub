param(
    [int]$Port = 8000
)

# 可复制的离线本地启动方式：SQLite + 内存检索 + Mock LLM。
$env:DATABASE_URL_OVERRIDE = "sqlite+aiosqlite:///./customer_service.db"
$env:USE_MILVUS = "false"
$env:MOCK_LLM_ENABLED = "true"
$env:REDIS_ENABLED = "false"
$env:DEBUG = "true"
$env:PORT = "$Port"

Write-Host "Starting AI Customer local profile on http://localhost:$Port"
Write-Host "Dependencies: SQLite / memory retrieval / Mock LLM (not production HA)"
python -m uvicorn app.main:app --host 0.0.0.0 --port $Port --reload
