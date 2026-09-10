param(
    [int]$Port = 8000
)

$env:APP_PORT = "$Port"
Write-Host "Starting AI Customer full Docker stack on http://localhost:$Port"
docker compose up -d --build
docker compose ps
