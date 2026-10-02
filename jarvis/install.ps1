# Jarvis Always-On installer: run ONCE per machine (homebase first, then the rig).
# In a normal (non-admin) PowerShell window on that machine, paste:
#
#   $t=[Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN','User'); if(!$t){$t=Read-Host 'GitHub token'; [Environment]::SetEnvironmentVariable('GITHUB_TASKS_TOKEN',$t,'User')}; irm -Headers @{Authorization="Bearer $t"; Accept='application/vnd.github.raw'} 'https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/install.ps1?ref=claude/eager-knuth-lakcxt' | iex
#
# What it does:
#   - updates Claude Code and runs `claude remote-control` once in a visible window so you can answer the
#     two one-time questions (trust the folder, enable Remote Control) -- these can't be pre-answered
#   - scheduled task "Jarvis Remote Control": starts the Remote Control server hidden at every logon
#   - scheduled task "Jarvis Watchdog": every 5 min restarts Remote Control if it died (it exits after
#     ~10 min offline) and updates this machine's Health issue in the GitHub tasks repo
#   - homebase only: never sleep on AC power, lid close does nothing on AC

$ErrorActionPreference = 'Stop'
$flowRef = 'claude/eager-knuth-lakcxt'
# Flow is private, so files come through the GitHub API with this user's GITHUB_TASKS_TOKEN
# (the token needs Contents: Read-only on flanneryjake/Flow).
function Get-FlowFile([string]$path, [string]$out) {
    $tok = if ($env:GITHUB_TASKS_TOKEN) { $env:GITHUB_TASKS_TOKEN } else { [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User') }
    $h = @{ Accept = 'application/vnd.github.raw'; 'User-Agent' = 'jarvis-installer' }
    if ($tok) { $h.Authorization = "Bearer $tok" }
    try { Invoke-WebRequest -UseBasicParsing -Headers $h "https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/$path`?ref=$flowRef" -OutFile $out }
    catch { throw "Could not download jarvis/$path from Flow ($_). Check that GITHUB_TASKS_TOKEN can read flanneryjake/Flow." }
}

function Say([string]$m, [string]$c = 'Cyan') { Write-Host $m -ForegroundColor $c }
$results = [ordered]@{}

# --- Which machine is this? -----------------------------------------------------------
switch ($env:COMPUTERNAME.ToUpper()) {
    'DESKTOP-5VE3C77' { $machine = 'homebase' }
    'DESKTOP-VLLDDM4' { $machine = 'rig' }
    'LAPTOP-4150EGRS' { $machine = 'laptop' }
    default {
        $machine = (Read-Host "Is this 'homebase', 'rig' or 'laptop'? (computer name $env:COMPUTERNAME)").Trim().ToLower()
        if ($machine -notin 'homebase', 'rig', 'laptop') { throw "Unknown machine '$machine'." }
    }
}
$workDir = if ($machine -eq 'homebase') { 'C:\Jarvis' } else { "$env:USERPROFILE\Desktop\Claude" }
$wdDir   = 'C:\Jarvis\watchdog'
$logDir  = Join-Path $wdDir 'logs'
New-Item -ItemType Directory -Force -Path $workDir, $wdDir, $logDir | Out-Null
Set-Content -Path (Join-Path $wdDir 'machine.txt') -Value $machine
Say "== Jarvis Always-On for $machine (work dir $workDir) =="

# --- Claude Code ---------------------------------------------------------------------
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) { throw "Claude Code ('claude') isn't on PATH for this user. Install it first, then re-run." }
Say 'Updating Claude Code...'
& claude update | Out-Host
foreach ($v in 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN') {
    if ([Environment]::GetEnvironmentVariable($v, 'User') -or [Environment]::GetEnvironmentVariable($v, 'Machine')) {
        Say "WARNING: $v is set. Remote Control needs a claude.ai login instead; remove that variable." 'Yellow'
    }
}

# --- Watchdog script -----------------------------------------------------------------
Get-FlowFile "watchdog.ps1" (Join-Path $wdDir 'watchdog.ps1')
$results['Watchdog script'] = "$wdDir\watchdog.ps1"

# --- GitHub token (for the health issue and the waiting-card count) --------------------
if ([Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User')) {
    $results['GitHub token'] = 'set (user env GITHUB_TASKS_TOKEN)'
} else {
    $results['GitHub token'] = 'MISSING - health issue will not update (set GITHUB_TASKS_TOKEN)'
}

# --- Power (homebase stays awake; the rig is allowed to sleep, homebase wakes it) -----
if ($machine -eq 'homebase') {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
    powercfg /setactive SCHEME_CURRENT
    $results['Power'] = 'never sleep on AC, lid close does nothing on AC'
}

# --- One-time interactive first run of Remote Control --------------------------------
Get-CimInstance Win32_Process -Filter "Name='claude.exe' OR Name='node.exe'" |
    Where-Object { $_.CommandLine -match 'remote-control' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Say ''
Say 'A new window will open running Remote Control.' 'Yellow'
Say "  - If it asks 'Trust $workDir? [y/N]', type y and press Enter." 'Yellow'
Say "  - If it asks 'Enable Remote Control? (y/n)', type y and press Enter." 'Yellow'
Say '  - If it says you are not signed in, type /login in a normal `claude` session, then re-run this installer.' 'Yellow'
Say '  - When it shows a session link / "connected", come back here and press Enter.' 'Yellow'
$first = Start-Process powershell -WorkingDirectory $workDir -PassThru -ArgumentList @(
    '-NoProfile', '-NoExit', '-Command', "Set-Location '$workDir'; claude remote-control --name '$machine'")
[void](Read-Host 'Press Enter once the other window says it is connected')
Get-CimInstance Win32_Process -Filter "Name='claude.exe' OR Name='node.exe'" |
    Where-Object { $_.CommandLine -match 'remote-control' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Stop-Process -Id $first.Id -Force -ErrorAction SilentlyContinue

# --- Scheduled tasks ------------------------------------------------------------------
$user      = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

# Remote Control keeps a hidden console (no output redirect), because without a terminal it refuses to start.
# Prefer npm's claude.cmd over its claude.ps1 shim, which won't load where the execution policy blocks scripts (the laptop).
$claudeExe = if (Get-Command claude.cmd -ErrorAction SilentlyContinue) { 'claude.cmd' } else { 'claude' }
$rcCmd = "Set-Location '$workDir'; $claudeExe remote-control --name '$machine' --permission-mode acceptEdits --verbose --debug-file '$logDir\remote-control-debug.log'"
$rcAction = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $workDir `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$rcCmd`""
Register-ScheduledTask -TaskName 'Jarvis Remote Control' -Action $rcAction -Principal $principal -Settings $settings `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $user) -Force | Out-Null

$wdAction  = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$wdDir\watchdog.ps1`""
$wdRepeat  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$wdLogon   = New-ScheduledTaskTrigger -AtLogOn -User $user
$wdSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Jarvis Watchdog' -Action $wdAction -Principal $principal -Settings $wdSettings `
    -Trigger @($wdRepeat, $wdLogon) -Force | Out-Null

Start-ScheduledTask -TaskName 'Jarvis Remote Control'
Say 'Waiting 25 s for Remote Control to connect...'
Start-Sleep -Seconds 25
& powershell -NoProfile -ExecutionPolicy Bypass -File "$wdDir\watchdog.ps1"

# --- Report ---------------------------------------------------------------------------
$snap = Get-Content (Join-Path $logDir 'last-snapshot.txt') -Raw -ErrorAction SilentlyContinue
$results['Scheduled tasks'] = 'Jarvis Remote Control (at logon), Jarvis Watchdog (every 5 min)'
Say ''
Say '== Done ==' 'Green'
$results.GetEnumerator() | ForEach-Object { Say ("  {0}: {1}" -f $_.Key, $_.Value) 'Green' }
Say ''
Say 'Health snapshot (also written to this machine's Health issue in flanneryjake/jarvis-tasks):'
Write-Host $snap
Say ''
Say "Check: in the Claude app's Code tab you should now see a session named '$machine'." 'Yellow'
