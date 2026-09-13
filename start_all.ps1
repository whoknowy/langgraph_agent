# ==========================================================
#  一键启动：LangGraph(2024) + Flask(5000) + Langfuse观测(Docker) + NATAPP
#  已在运行的服务会自动跳过，可重复执行
#  关闭：逐个关掉弹出的窗口即可
# ==========================================================

$proj   = "D:\langgraph\Customer-Agent"
$py     = "$proj\.venv\Scripts\python.exe"
$lg     = "$proj\.venv\Scripts\langgraph.exe"
$natapp = "D:\NATAPP\run_natapp.bat"

function Test-Up($url) {
    try { $null = Invoke-WebRequest -Uri $url -TimeoutSec 2 -UseBasicParsing; $true }
    catch { $false }
}

function Wait-Up($url, $name, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Up $url) { Write-Host "  $name 已就绪" -ForegroundColor Green; return }
        Start-Sleep -Seconds 2
    }
    Write-Host "  [警告] $name 在 $seconds 秒内未就绪，请看它的日志窗口排查" -ForegroundColor Yellow
}

# 若 Vue 构建产物缺失，自动执行一次前端构建
if (-not (Test-Path "$proj\frontend\dist\index.html")) {
    Write-Host "`n[0/4] 构建 Vue 3 前端..." -ForegroundColor Cyan
    Push-Location "$proj\frontend"
    npm install --no-audit --no-fund
    npm run build
    Pop-Location
}

Write-Host "`n[1/4] LangGraph 智能体服务（端口 2024）" -ForegroundColor Cyan
if (Test-Up "http://127.0.0.1:2024/ok") {
    Write-Host "  已在运行，跳过" -ForegroundColor DarkGray
} else {
    Start-Process cmd -ArgumentList "/k", "title LangGraph-2024 && cd /d $proj && $lg dev --no-browser --no-reload"
    Wait-Up "http://127.0.0.1:2024/ok" "LangGraph" 60
}

Write-Host "`n[2/4] Flask Web 服务（端口 5000）" -ForegroundColor Cyan
if (Test-Up "http://127.0.0.1:5000/api/health") {
    Write-Host "  已在运行，跳过" -ForegroundColor DarkGray
} else {
    Start-Process cmd -ArgumentList "/k", "title Flask-5000 && cd /d $proj && $py web_app.py"
    Wait-Up "http://127.0.0.1:5000/api/health" "Flask" 30
}

# Langfuse 是可选观测栈：没启动时后端自动 no-op（仅日志有导出重试噪音，不影响业务）
Write-Host "`n[3/4] Langfuse LLM 观测栈（Docker，可选）" -ForegroundColor Cyan
$dockerOk = $false
try { docker info *> $null; if ($LASTEXITCODE -eq 0) { $dockerOk = $true } } catch {}
if (-not $dockerOk) {
    Write-Host "  [跳过] Docker 未运行。先启动 Docker Desktop 再执行：" -ForegroundColor Yellow
    Write-Host "    docker compose up -d clickhouse minio postgres redis langfuse-worker langfuse-web" -ForegroundColor Yellow
    Write-Host "  （不启动 Langfuse 也不影响业务，仅 .env 密钥已配置时日志会有导出重试提示）" -ForegroundColor DarkGray
} elseif (Test-Up "http://127.0.0.1:3000/") {
    Write-Host "  已在运行，跳过" -ForegroundColor DarkGray
} else {
    Push-Location $proj
    docker compose up -d clickhouse minio postgres redis langfuse-worker langfuse-web
    Pop-Location
    Wait-Up "http://127.0.0.1:3000/" "Langfuse UI" 120
}

Write-Host "`n[4/4] NATAPP 内网穿透" -ForegroundColor Cyan
Start-Process cmd -ArgumentList "/k", "title NATAPP-隧道 && $natapp"

Write-Host @"

==================================================
 全部启动完成！
   本地访问:  http://127.0.0.1:5000
   管理端:    http://127.0.0.1:5000/admin
   Langfuse:  http://localhost:3000 （admin@demo.local / demo123456）
   公网地址:  http://flightagent.nat100.top （付费固定域名，不再变化）
   关闭方法:  逐个关掉 LangGraph-2024 / Flask-5000 /
             NATAPP-隧道 窗口；Langfuse 用
             docker compose stop clickhouse minio postgres redis langfuse-worker langfuse-web
==================================================
"@ -ForegroundColor Cyan
