# 一键启动：docker compose 拉起完整依赖栈与应用。
# 用法： .\scripts\start.ps1 [-NoMigrate] [-SkipSeed] [-Langfuse]
param(
    [switch]$NoMigrate,   # 跳过 alembic 迁移
    [switch]$SkipSeed,    # 跳过灌入演示数据
    [switch]$Langfuse,    # 同时拉起自托管 Langfuse 观测栈（docker-compose.langfuse.yml）
    [int]$Port = 8000
)

Set-StrictMode -Version Latest
# 用 Continue 而非 Stop：docker 会把构建/进度信息写到 stderr，
# Stop 模式下 PowerShell 5.1 会把任何原生 stderr 当致命错误中断脚本。
# 脚本对每个原生命令都显式检查 $LASTEXITCODE，错误处理不受影响。
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 启动 AI Customer（Docker 全栈）" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. 准备 .env
$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "[1/4] 未找到 .env，从 .env.example 复制…" -ForegroundColor Yellow
    Copy-Item (Join-Path $Root ".env.example") $envFile
} else {
    Write-Host "[1/4] 检测到 .env，直接使用。" -ForegroundColor Green
}

# 2. 检查占位密码，避免使用默认配置启动
Write-Host "[2/4] 校验关键配置…"
$placeholders = @(
    @{ Name = "MYSQL_ROOT_PASSWORD"; Value = "change-me-root-password" },
    @{ Name = "MYSQL_APP_PASSWORD";  Value = "change-me-app-password" },
    @{ Name = "MINIO_ROOT_PASSWORD"; Value = "change-me-minio-password" }
)
foreach ($p in $placeholders) {
    $v = (Select-String -Path $envFile -Pattern "^$($p.Name)=").Line
    if ($v -and $v.Split("=", 2)[1] -eq $p.Value) {
        Write-Warning "$($p.Name) 仍为占位值，请先编辑 .env 设置真实密码。"
    }
}

# 3. 启动依赖与应用（可选叠加 Langfuse 自托管观测栈）
Write-Host "[3/4] docker compose up -d --build …"
Push-Location $Root
try {
    if ($Langfuse) {
        $composeFile = Join-Path $Root "docker-compose.langfuse.yml"
        if (-not (Test-Path $composeFile)) {
            throw "未找到 $composeFile，-Langfuse 需要仓库内的可选编排文件。"
        }
        Write-Host "  -Langfuse 已开启：叠加自托管 Langfuse（http://localhost:3000）" -ForegroundColor Cyan
        docker compose -f docker-compose.yml -f $composeFile up -d --build
    } else {
        docker compose up -d --build
    }
    if ($LASTEXITCODE -ne 0) { throw "docker compose up 失败（退出码 $LASTEXITCODE）" }

    # 4. 可选：数据库迁移
    if (-not $NoMigrate) {
        Write-Host "[4/4] 执行数据库迁移：alembic upgrade head …"
        docker compose run --rm app alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "alembic 迁移失败（退出码 $LASTEXITCODE）" }
    }

    # 可选：灌入演示数据
    if (-not $SkipSeed) {
        $seedMark = Join-Path $Root ".seed_done"
        if (-not (Test-Path $seedMark)) {
            Write-Host "灌入演示数据（订单 + 知识库）…"
            docker compose exec app python scripts/seed_ecommerce_orders.py
            if ($LASTEXITCODE -ne 0) { Write-Warning "订单演示数据导入失败（退出码 $LASTEXITCODE），服务仍已启动。" }
            docker compose exec app python scripts/import_rag_documents.py --clear
            if ($LASTEXITCODE -ne 0) { Write-Warning "知识库演示数据导入失败（退出码 $LASTEXITCODE），服务仍已启动。" }
            New-Item -ItemType File -Path $seedMark -Force | Out-Null
        } else {
            Write-Host "演示数据已初始化（跳过，如需重灌请删除 $seedMark）。" -ForegroundColor DarkGray
        }
    }
} finally {
    Pop-Location
}

# 端口显示：优先 .env 的 APP_PORT（compose 实际映射），避免 -Port 参数与实际不一致
$appPort = $Port
$portLine = Select-String -Path $envFile -Pattern "^APP_PORT=" -ErrorAction SilentlyContinue
if ($portLine) {
    $appPort = $portLine.Line.Split("=", 2)[1].Trim()
}

Write-Host ""
docker compose ps
Write-Host ""
Write-Host "启动完成：" -ForegroundColor Green
Write-Host "  应用首页     http://localhost:$appPort/"
Write-Host "  健康检查     http://localhost:$appPort/health"
Write-Host "  OpenAPI 文档 http://localhost:$appPort/docs"
if ($Langfuse) {
    Write-Host "  Langfuse 观测 http://localhost:3000" -ForegroundColor Green
}
Write-Host "停止服务请运行 scripts\stop.ps1" -ForegroundColor Yellow