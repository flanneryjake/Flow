# Jarvis fleet control: run ONCE per PC (homebase, rig, laptop), after install-ghq.ps1.
# In a normal PowerShell window, paste:
#
#   irm https://raw.githubusercontent.com/flanneryjake/Flow/claude/eager-knuth-lakcxt/jarvis/fleet/install-fleet.ps1 | iex
#
# What it does:
#   - saves fleet.py, fleet_api.py, fleet-panel.js and roles.json to C:\Jarvis\fleet, and adds the Worker gate
#     (fleet_allows) to this PC's own C:\Jarvis\ghq\ghq.py without replacing the rest of it
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
Say "Saved the fleet scripts to $dir"

# Add the Worker gate to this PC's own ghq.py instead of replacing the file, since a PC can carry newer local
# changes (e.g. the "now" lane). Skipped if the gate is already there.
$ghqFile = 'C:\Jarvis\ghq\ghq.py'
if (Test-Path $ghqFile) {
    $src = [IO.File]::ReadAllText($ghqFile)
    if ($src -notmatch 'def fleet_allows') {
        $gateFn = @'
def fleet_allows(machine):
    """False when the Fleet panel has this machine paused or disconnected. True if fleet.py isn't installed."""
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fleet'), r'C:\Jarvis\fleet'):
        if os.path.exists(os.path.join(p, 'fleet.py')):
            if p not in sys.path:
                sys.path.insert(0, p)
            break
    try:
        import fleet
    except ImportError:
        return True
    return fleet.may_take_cards(machine)


'@
        $gateFn += "`n"  # two blank lines before def claim, as in Flow's ghq.py
        $gateCall = "    if not fleet_allows(machine):`n        return []  # paused or disconnected from the phone app's Fleet panel (fleet/fleet.py)`n"
        $nl = if ($src -match "`r`n") { "`r`n" } else { "`n" }
        $rx = [regex]'(?s)(def ready\(machine[^\n]*\n\s+"""(?:(?!""").)*"""\r?\n)'
        if (-not $rx.IsMatch($src) -or $src -notmatch 'def claim\(') {
            throw 'ghq.py has an unexpected layout; add the fleet gate by hand (see jarvis/ghq/ghq.py in Flow).'
        }
        Copy-Item $ghqFile "$ghqFile.bak-fleet-$(Get-Date -Format yyyyMMdd-HHmmss)"
        $src = $rx.Replace($src, '$1' + $gateCall.Replace("`n", $nl), 1)
        $i = $src.IndexOf('def claim(')
        $src = $src.Substring(0, $i) + $gateFn.Replace("`r`n", "`n").Replace("`n", $nl) + $src.Substring($i)
        [IO.File]::WriteAllText($ghqFile, $src)
        Say 'Added the fleet gate to ghq.py (backup kept next to it).'
    }
} else {
    Invoke-WebRequest -UseBasicParsing "$base/ghq/ghq.py" -OutFile $ghqFile
}

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
