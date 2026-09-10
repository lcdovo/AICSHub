# 一键停止：关闭 Docker 全栈服务，保留数据卷。
# 用法： .\scripts\stop.ps1 [-Data]   （加 -Data 会同时删除 MySQL/Redis/Milvus 等数据卷）
param(
    [switch]$Data
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
    if ($Data) {
        Write-Host "停止并清除全部数据卷（不可恢复）…" -ForegroundColor Yellow
        docker compose down -v
        if ($LASTEXITCODE -ne 0) { throw "docker compose down 失败（退出码 $LASTEXITCODE）" }
        # 数据卷已清空，必须同步清除 seed 标记，否则下次 start 会误判"已初始化"而跳过灌数据
        $seedMark = Join-Path $Root ".seed_done"
        if (Test-Path $seedMark) {
            Remove-Item $seedMark -Force
            Write-Host "已清除 .seed_done 标记，下次 start 将重新灌入演示数据。" -ForegroundColor Yellow
        }
    } else {
        docker compose down
        if ($LASTEXITCODE -ne 0) { throw "docker compose down 失败（退出码 $LASTEXITCODE）" }
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "服务已停止。如需重新启动，请运行 scripts\start.ps1" -ForegroundColor Green