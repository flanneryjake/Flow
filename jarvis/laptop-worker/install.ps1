# Installs the Baby Jarvis laptop Worker on LAPTOP-4150EGRS (run in a normal PowerShell window from this folder):
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
# Copies laptop_worker.py and laptop_watchdog.py to %USERPROFILE%\JarvisAgent and registers one scheduled task,
# "Jarvis Baby Watchdog", that runs pythonw.exe every 5 min and at logon. pythonw has no console, so nothing
# flashes. The watchdog starts the Worker and Ollama when they are down.
$ErrorActionPreference = 'Stop'
$dest = Join-Path $env:USERPROFILE 'JarvisAgent'
New-Item -ItemType Directory -Force -Path $dest, (Join-Path $dest 'logs'), 'C:\Jarvis\outputs\laptop' | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'laptop_worker.py'), (Join-Path $PSScriptRoot 'laptop_watchdog.py') $dest -Force

$py = (Get-Command python.exe -All | Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1).Source
if (-not $py) { throw 'Python 3 is not installed (the WindowsApps python stub does not count).' }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { throw "pythonw.exe not found next to $py" }
if (-not [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'User')) { Write-Warning 'NOTION_TOKEN is not set for this user; the Worker will idle until it is.' }

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$dest\laptop_watchdog.py`"" -WorkingDirectory $dest
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Jarvis Baby Watchdog' -Action $action -Principal $principal -Settings $settings `
    -Trigger @($every, $logon) -Description 'Keeps Ollama and the Baby Jarvis laptop Worker running (no window).' -Force | Out-Null
Start-ScheduledTask -TaskName 'Jarvis Baby Watchdog'
Write-Host "Installed to $dest; task 'Jarvis Baby Watchdog' runs $pyw every 5 min." -ForegroundColor Green
