# ==========================================================
#  一键启动：LangGraph(2024) + Flask(5000) + NATAPP 内网穿透
#  已在运行的服务会自动跳过，可重复执行
#  关闭：逐个关掉弹出的三个窗口即可
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

Write-Host "`n[1/3] LangGraph 智能体服务（端口 2024）" -ForegroundColor Cyan
if (Test-Up "http://127.0.0.1:2024/ok") {
    Write-Host "  已在运行，跳过" -ForegroundColor DarkGray
} else {
    Start-Process cmd -ArgumentList "/k", "title LangGraph-2024 && cd /d $proj && $lg dev --no-browser --no-reload"
    Wait-Up "http://127.0.0.1:2024/ok" "LangGraph" 60
}

Write-Host "`n[2/3] Flask Web 服务（端口 5000）" -ForegroundColor Cyan
if (Test-Up "http://127.0.0.1:5000/api/health") {
    Write-Host "  已在运行，跳过" -ForegroundColor DarkGray
} else {
    Start-Process cmd -ArgumentList "/k", "title Flask-5000 && cd /d $proj && $py web_app.py"
    Wait-Up "http://127.0.0.1:5000/api/health" "Flask" 30
}

Write-Host "`n[3/3] NATAPP 内网穿透" -ForegroundColor Cyan
Start-Process cmd -ArgumentList "/k", "title NATAPP-隧道 && $natapp"

Write-Host @"

==================================================
 全部启动完成！
   本地访问:  http://127.0.0.1:5000
   管理端:    http://127.0.0.1:5000/admin
   公网地址:  http://flightagent.nat100.top （付费固定域名，不再变化）
   关闭方法:  逐个关掉 LangGraph-2024 / Flask-5000 /
             NATAPP-隧道 三个窗口
==================================================
"@ -ForegroundColor Cyan
