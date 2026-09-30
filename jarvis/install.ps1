# Jarvis Always-On installer: run ONCE per machine (homebase first, then the rig).
# In a normal (non-admin) PowerShell window on that machine, paste:
#
#   irm https://raw.githubusercontent.com/flanneryjake/Flow/main/jarvis/install.ps1 | iex
#
# What it does:
#   - updates Claude Code and runs `claude remote-control` once in a visible window so you can answer the
#     two one-time questions (trust the folder, enable Remote Control) -- these can't be pre-answered
#   - scheduled task "Jarvis Remote Control": starts the Remote Control server hidden at every logon
#   - scheduled task "Jarvis Watchdog": every 5 min restarts Remote Control if it died (it exits after
#     ~10 min offline) and updates this machine's row in the Notion Machine Health table
#   - homebase only: never sleep on AC power, lid close does nothing on AC

$ErrorActionPreference = 'Stop'
$base = 'https://raw.githubusercontent.com/flanneryjake/Flow/main/jarvis'

function Say([string]$m, [string]$c = 'Cyan') { Write-Host $m -ForegroundColor $c }
$results = [ordered]@{}

# --- Which machine is this? -----------------------------------------------------------
switch ($env:COMPUTERNAME.ToUpper()) {
    'DESKTOP-5VE3C77' { $machine = 'homebase' }
    'DESKTOP-VLLDDM4' { $machine = 'rig' }
    default {
        $machine = (Read-Host "Is this 'homebase' or 'rig'? (computer name $env:COMPUTERNAME)").Trim().ToLower()
        if ($machine -notin 'homebase', 'rig') { throw "Unknown machine '$machine'." }
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
Invoke-WebRequest -UseBasicParsing "$base/watchdog.ps1" -OutFile (Join-Path $wdDir 'watchdog.ps1')
$results['Watchdog script'] = "$wdDir\watchdog.ps1"

# --- Notion token (for the health row) -- ------------------------------------------------
$token = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'User')
if (-not $token) { $token = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'Machine') }
if (-not $token) {
    # The Worker already has one; look in its config files before asking.
    $pattern = '(ntn_[A-Za-z0-9]{30,}|secret_[A-Za-z0-9]{30,})'
    $hit = Get-ChildItem -Path 'C:\Jarvis', "$env:USERPROFILE\JarvisAgent" -Recurse -File -Include *.env, *.json, *.txt, *.ini, *.cfg, *.ps1, *.py -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -lt 200KB -and $_.FullName -notmatch '\\logs\\|\\work\\' } |
        Select-String -Pattern $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($hit) { $token = $hit.Matches[0].Value; Say "Found the Notion token the Worker uses ($($hit.Path))." }
}
if (-not $token) {
    Say 'Copy your Notion integration token (Notion > Jarvis > Configuration), then press Enter here.' 'Yellow'
    [void](Read-Host)
    $token = "$(Get-Clipboard -Raw)".Trim()
    Set-Clipboard -Value ' '
}
if ($token -match '^(ntn_|secret_)') {
    [Environment]::SetEnvironmentVariable('NOTION_TOKEN', $token, 'User')
    $results['Notion token'] = 'set (user env NOTION_TOKEN)'
} else {
    $results['Notion token'] = 'MISSING - health row will not update'
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
$rcCmd = "Set-Location '$workDir'; claude remote-control --name '$machine' --permission-mode acceptEdits --verbose --debug-file '$logDir\remote-control-debug.log'"
$rcAction = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $workDir `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$rcCmd`""
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
Say 'Health snapshot (also written to the Machine Health table in Notion):'
Write-Host $snap
Say ''
Say "Check: in the Claude app's Code tab you should now see a session named '$machine'." 'Yellow'
