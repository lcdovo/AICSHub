# 一键停止：关闭 Docker 全栈服务，保留数据卷。
# 用法： .\scripts\stop.ps1 [-Data] [-Langfuse] [-Reranker] [-LocalEmbedding] [-LocalModels] [-All]
#   -Data        加 -Data 会同时删除 MySQL/Redis/Milvus 等数据卷（不可恢复）
#   -Langfuse    与 start.ps1 -Langfuse 配对，同时停止 Langfuse 观测栈
#   -Reranker    与 start.ps1 -Reranker 配对，同时停止本地 bge-reranker-large
#   -LocalModels 与 start.ps1 -LocalModels 配对，同时停止本地 vLLM/Ollama 推理栈
#   -LocalEmbedding 与 start.ps1 -LocalEmbedding 配对，仅启用本地向量服务的 profile
#   -All         等价于 -Langfuse -Reranker -LocalModels
param(
    [switch]$Data,
    [switch]$Langfuse,
    [switch]$Reranker,
    [switch]$LocalEmbedding,
    [switch]$LocalModels,
    [switch]$All
)

Set-StrictMode -Version Latest
# 同 start.ps1：docker 原生 stderr 在 Stop 模式下会被误判为致命错误。
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 停止 AI Customer（Docker 全栈）" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Push-Location $Root
try {
    $composeArgs = @("down")
    if ($Data) { $composeArgs += "-v" }
    if ($Langfuse -or $All) {
        $composeArgs = @("--profile", "langfuse") + $composeArgs
        Write-Host "  profile[langfuse] 已开启：一并停止 Langfuse 观测栈" -ForegroundColor Cyan
    }
    if ($Reranker -or $All) {
        $composeArgs = @("--profile", "reranker") + $composeArgs
        Write-Host "  profile[reranker] 已开启：一并停止本地 bge-reranker-large" -ForegroundColor Cyan
    }
    if ($LocalModels -or $All) {
        $composeArgs = @("--profile", "local-models") + $composeArgs
        Write-Host "  profile[local-models] 已开启：一并停止本地 vLLM/Ollama 推理栈" -ForegroundColor Cyan
    } elseif ($LocalEmbedding) {
        $composeArgs = @("--profile", "local-embedding") + $composeArgs
        Write-Host "  profile[local-embedding] 已开启：一并停止本地 Embedding" -ForegroundColor Cyan
    }
    docker compose @composeArgs
    if ($LASTEXITCODE -ne 0) { throw "docker compose down 失败（退出码 $LASTEXITCODE）" }
    # 数据卷已清空，必须同步清除 seed 标记，否则下次 start 会误判"已初始化"而跳过灌数据
    if ($Data) {
        $seedMark = Join-Path $Root ".seed_done"
        if (Test-Path $seedMark) {
            Remove-Item $seedMark -Force
            Write-Host "已清除 .seed_done 标记，下次 start 将重新灌入演示数据。" -ForegroundColor Yellow
        }
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "服务已停止。如需重新启动，请运行 scripts\start.ps1" -ForegroundColor Green
