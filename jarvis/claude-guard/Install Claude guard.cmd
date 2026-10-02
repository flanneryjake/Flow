@echo off
rem Installs the Jarvis Claude guard on this PC. Double-click it; it asks for admin, then downloads
rem jarvis/claude-guard/install-claude-guard.ps1 from Flow with this user's GITHUB_TASKS_TOKEN and runs it.
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN','User'); irm -Headers @{Authorization=('Bearer '+$t); Accept='application/vnd.github.raw'} 'https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/claude-guard/install-claude-guard.ps1?ref=claude/eager-knuth-lakcxt' | iex"
pause
