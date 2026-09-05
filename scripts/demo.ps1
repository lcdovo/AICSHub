param([string]$BaseUrl = "http://localhost:8000")
$ErrorActionPreference = "Continue"

function Invoke-Demo([string]$Method, [string]$Path, $Body = $null, [string]$Token = "") {
    $headers = @{}
    if ($Token) { $headers.Authorization = "Bearer $Token" }
    try {
        $params = @{ Method = $Method; Uri = "$BaseUrl$Path"; Headers = $headers; ContentType = "application/json" }
        if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Depth 8) }
        $result = Invoke-RestMethod @params
        Write-Host "[$Method $Path] OK"
        return $result
    } catch {
        Write-Warning "[$Method $Path] $($_.Exception.Message)"
        return $null
    }
}

Write-Host "AI Customer 5 分钟本地演示：$BaseUrl"
$admin = Invoke-Demo POST "/api/v1/users/login" @{ username = "admin"; password = "admin12345" }
$user = Invoke-Demo POST "/api/v1/users/login" @{ username = "user001"; password = "user12345" }
$adminToken = if ($admin) { $admin.data.access_token } else { "" }
$userToken = if ($user) { $user.data.access_token } else { "" }
Invoke-Demo POST "/api/v1/knowledge/documents/seed" $null $adminToken | Out-Null
Invoke-Demo POST "/api/v1/knowledge/rag-query" @{ query = "签收后多久可以无理由退货"; top_k = 3; use_llm = $false } $userToken | Out-Null
$chat = Invoke-Demo POST "/api/v1/chat/send" @{ user_id = 1; message = "请查询订单 ORD20240001" } $userToken
$sessionId = if ($chat) { $chat.session_id } else { $null }
if ($sessionId) {
    Invoke-Demo POST "/api/v1/chat/send" @{ user_id = 1; session_id = $sessionId; message = "我想申请退款，订单号 ORD20240001" } $userToken | Out-Null
    Invoke-Demo POST "/api/v1/chat/send" @{ user_id = 1; session_id = $sessionId; message = "确认" } $userToken | Out-Null
}
Invoke-Demo POST "/api/v1/chat/send" @{ user_id = 1; session_id = $sessionId; message = "我想转人工" } $userToken | Out-Null
Invoke-Demo POST "/api/v1/tickets" @{ user_id = 1; category = "demo"; content = "演示工单：物流咨询"; priority = "medium"; idempotency_key = "demo-ticket-001" } $userToken | Out-Null
if ($sessionId) {
    Invoke-Demo POST "/api/v1/handoff" @{ user_id = 1; session_id = $sessionId; reason = "演示转人工"; priority = "normal"; idempotency_key = "demo-handoff-001" } $userToken | Out-Null
} else {
    Write-Warning "聊天未返回 session_id，跳过转人工演示"
}
Invoke-Demo GET "/health" | Out-Null
Write-Host "演示完成：请打开 $BaseUrl/docs 查看 OpenAPI，或运行 python scripts/rag_benchmark.py 查看检索基线。"
