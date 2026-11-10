# 日常启停：复用已有容器及其镜像、环境变量、挂载和启动命令。
# 首次安装或源码部署请使用 start.ps1；本入口不构建、不迁移、不导入数据。
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "stop")]
    [string]$Action
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

try {
    Push-Location $Root
    try {
        # 只选择默认主栈及双击入口对应的模型服务，不启动本地聊天模型。
        $services = @("mysql", "redis", "etcd", "minio", "milvus", "embedding-local", "reranker", "app", "worker")
        $composeArgs = @("--profile", "reranker", "--profile", "local-embedding")
        # 不打印容器配置，避免环境变量中的密钥进入控制台。
        $existing = @(& docker compose @composeArgs ps -a --format '{{.Service}}')
        if ($LASTEXITCODE -ne 0) { throw "无法读取已有容器，请检查 Docker Desktop。" }
        $selected = @($services | Where-Object { $_ -in $existing })
        if ($Action -eq "start") {
            foreach ($required in @("app", "worker")) {
                if ($required -notin $selected) {
                    throw "缺少 $required 容器。首次安装请运行 scripts/start.ps1 -SkipSeed；已有部署请按对应发布说明恢复。"
                }
            }
        }
        if ($selected.Count -eq 0) {
            Write-Host "没有需要停止的已有容器。"
            exit 0
        }
        & docker compose @composeArgs $Action @selected
        if ($LASTEXITCODE -ne 0) { throw "容器 $Action 失败，请检查 Docker 状态。" }
        & docker compose @composeArgs ps -a
        if ($LASTEXITCODE -ne 0) { throw "无法读取启停后的容器状态。" }
        Write-Host "操作完成；容器状态见上表，start 命令完成不代表所有服务已经健康。"
    } finally {
        Pop-Location
    }
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
