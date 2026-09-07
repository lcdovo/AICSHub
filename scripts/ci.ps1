param(
    [switch]$BuildImage
)

$ErrorActionPreference = "Stop"
$env:DATABASE_URL_OVERRIDE = "sqlite+aiosqlite:///./tests/.ci.db"
$env:USE_MILVUS = "false"
$env:REDIS_ENABLED = "false"
$env:DEBUG = "false"
$env:AUTH_SECRET_KEY = "ci-secret-key-for-local-suite-32chars"
$env:AUTH_DEMO_ACCOUNTS_ENABLED = "true"

python -m compileall app scripts
python -m ruff check app scripts --select E9,F63,F7,F82
python -m mypy app/config/config.py app/schemas/schemas.py --ignore-missing-imports --explicit-package-bases
python -m pip_audit -r requirements.txt --progress-spinner off
if ($BuildImage) {
    docker build --tag ai-customer:ci .
}
Write-Host "CI checks passed (compileall + ruff + mypy + pip_audit$(if ($BuildImage) { ' + docker build' }))"
