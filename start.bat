@echo off
rem Double-click launcher -> start_all.ps1 (PowerShell 7)
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1"
) else (
    "C:\Program Files\PowerShell\7\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1"
)
pause
