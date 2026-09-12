# 运行 JMeter 阶梯压测（Windows PowerShell）
# 前置：本机已装 JMeter 5.6+（jmeter 命令在 PATH），被测服务已启动。
# 用法：在仓库根目录执行  pwsh -File ops/jmeter/run_jmeter.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # 仓库根目录
Set-Location $root

if (-not (Get-Command jmeter -ErrorAction SilentlyContinue)) {
    Write-Host "✗ 未找到 jmeter 命令。请安装 JMeter 5.6+（需 JDK 8+）并加入 PATH。" -ForegroundColor Red
    Write-Host "  零依赖替代：.venv/Scripts/python.exe ops/loadtest/step_load.py"
    exit 1
}

# 预检被测服务
try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/health" -TimeoutSec 3
} catch {
    Write-Host "✗ Flask 未启动（http://127.0.0.1:5000/api/health 不可达）。先运行 .venv/Scripts/python.exe web_app.py" -ForegroundColor Red
    exit 1
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force -Path "ops/jmeter/results" | Out-Null

Write-Host "▶ 阶梯压测 10→50→100→200，每组 60s …"
jmeter -n -t "ops/jmeter/flight_agent.jmx" `
       -l "ops/jmeter/results/jmeter_run_$ts.csv" `
       -e -o "ops/jmeter/results/report_$ts"

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ 完成。HTML 报告：ops/jmeter/results/report_$ts/index.html" -ForegroundColor Green
    Write-Host "  CSV 明细：ops/jmeter/results/jmeter_run_$ts.csv"
} else {
    Write-Host "✗ JMeter 退出码 $LASTEXITCODE" -ForegroundColor Red
}
