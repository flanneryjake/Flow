# Jarvis fleet control: run ONCE per PC (homebase, rig, laptop), after install-ghq.ps1.
# In a normal PowerShell window, paste:
#
#   irm https://raw.githubusercontent.com/flanneryjake/Flow/claude/eager-knuth-lakcxt/jarvis/fleet/install-fleet.ps1 | iex
#
# What it does:
#   - saves fleet.py, fleet_api.py, fleet-panel.js and roles.json to C:\Jarvis\fleet, and the updated ghq.py
#     (its Worker gate) to C:\Jarvis\ghq
#   - registers the "Jarvis Fleet" task: `fleet.py tick` every 2 minutes, no window. The tick writes this PC's
#     heartbeat on the pinned "Fleet control" issue, applies its mode (pause / leave or rejoin the tailnet) and
#     hands back cards stranded on a PC that went quiet.
# Nothing is disconnected by installing; every PC starts as active.

$ErrorActionPreference = 'Stop'
$base = 'https://raw.githubusercontent.com/flanneryjake/Flow/claude/eager-knuth-lakcxt/jarvis'
$dir  = 'C:\Jarvis\fleet'
function Say([string]$m, [string]$c = 'Cyan') { Write-Host $m -ForegroundColor $c }

if (-not [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User')) {
    throw 'GITHUB_TASKS_TOKEN is not set for this user. Run install-ghq.ps1 first.'
}
$py = Get-Command python, py -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $py) { throw 'Python is not on PATH for this user.' }
$pyw = Join-Path (Split-Path $py.Source) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py.Source }

New-Item -ItemType Directory -Force -Path $dir, 'C:\Jarvis\ghq', 'C:\Jarvis\logs' | Out-Null
foreach ($f in 'fleet.py', 'fleet_api.py', 'fleet-panel.js', 'roles.json') {
    Invoke-WebRequest -UseBasicParsing "$base/fleet/$f" -OutFile (Join-Path $dir $f)
}
Invoke-WebRequest -UseBasicParsing "$base/ghq/ghq.py" -OutFile 'C:\Jarvis\ghq\ghq.py'
Say "Saved the fleet scripts to $dir"

$env:GITHUB_TASKS_TOKEN = [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User')
& $py.Source (Join-Path $dir 'fleet.py') tick
if ($LASTEXITCODE -ne 0) { throw 'The first tick failed; see the error above.' }

$user = "$env:USERDOMAIN\$env:USERNAME"
$cmd = "`"$dir\fleet.py`" tick >> `"C:\Jarvis\logs\fleet.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"`"$pyw`" $cmd`"" -WorkingDirectory $dir
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 2) -RepetitionDuration (New-TimeSpan -Days 3650)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'Jarvis Fleet' -Action $action -Trigger @($every, $logon) -Settings $settings `
    -Principal $principal -Force | Out-Null
Say 'Registered the "Jarvis Fleet" task (every 2 minutes).'

$hide = Join-Path $env:TEMP 'hide-task-windows.ps1'
try {
    Invoke-WebRequest -UseBasicParsing "$base/hide-task-windows.ps1" -OutFile $hide
    & powershell -NoProfile -ExecutionPolicy Bypass -File $hide | Out-Null
} catch { Say "Could not run hide-task-windows.ps1 ($_); the task may flash a window." 'Yellow' }

& $py.Source (Join-Path $dir 'fleet.py') status
Say 'Done. Fleet board: https://github.com/flanneryjake/jarvis-tasks/issues?q=label%3Afleet' 'Green'
