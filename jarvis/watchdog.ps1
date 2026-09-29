# Jarvis watchdog. Runs every 5 min as a scheduled task (installed by install.ps1).
#  1. Keeps `claude remote-control` alive, so phone/cloud Claude sessions can always reach this PC.
#  2. Posts a health snapshot to this machine's Heartbeat card in the Notion Tasks board, so a cloud
#     session (which can't get onto Tailscale) can see what this PC is doing and why a job is stuck.
# It never runs Notion cards or anything else: its only actions are starting the Remote Control task
# and writing one Notion property.

$ErrorActionPreference = 'Continue'
$root    = $PSScriptRoot
$machine = (Get-Content (Join-Path $root 'machine.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $machine) { $machine = $env:COMPUTERNAME }
$heartbeatCards = @{
    homebase = '3ea11c3639af818fa61bd67059916e7e'
    rig      = '3ea11c3639af814fb035f549694f4ad1'
}
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'watchdog.log'
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.old" -Force }
function Log([string]$m) { "$(Get-Date -Format 'MM/dd HH:mm:ss') $m" | Add-Content -Path $log }
function Redact([string]$s) { $s -replace '(ntn_|secret_|sk-ant-|sk-)[A-Za-z0-9_\-]{16,}', '<redacted>' }

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("$(Get-Date -Format 'MM/dd HH:mm') $machine watchdog")

# --- 1. Remote Control ---------------------------------------------------------------
$rcProc = Get-CimInstance Win32_Process -Filter "Name='claude.exe' OR Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'remote-control' }
if ($rcProc) {
    $lines.Add("Remote Control: UP (pid $(@($rcProc)[0].ProcessId))")
} else {
    $task = Get-ScheduledTask -TaskName 'Jarvis Remote Control' -ErrorAction SilentlyContinue
    if ($task) {
        Start-ScheduledTask -TaskName 'Jarvis Remote Control'
        Log 'Remote Control was down; started the task.'
        $lines.Add('Remote Control: WAS DOWN, restarted just now')
    } else {
        $lines.Add('Remote Control: task missing (re-run install.ps1)')
    }
}
$rcDebug = Join-Path $logDir 'remote-control-debug.log'
if (Test-Path $rcDebug) {
    $bad = Select-String -Path $rcDebug -Pattern 'not trusted|requires a claude.ai|full-scope|not yet enabled|Enable Remote Control\?|trusted-device|could not|error' -SimpleMatch:$false |
        Select-Object -Last 3
    foreach ($b in $bad) {
        $l = (Redact $b.Line).Trim()
        $lines.Add('  RC log: ' + $l.Substring(0, [Math]::Min(200, $l.Length)))
    }
}

# --- 2. Listening ports ---------------------------------------------------------------
$ports = if ($machine -eq 'rig') { @{ 'ollama' = 11434 } } else { @{ 'hub v2' = 8765; 'hub v3' = 8770; 'agent' = 8790 } }
$portStatus = foreach ($k in $ports.Keys) {
    $up = Get-NetTCPConnection -State Listen -LocalPort $ports[$k] -ErrorAction SilentlyContinue
    "$k :$($ports[$k]) " + $(if ($up) { 'up' } else { 'DOWN' })
}
$lines.Add('Ports: ' + ($portStatus -join ', '))

# --- 3. Jarvis scheduled tasks (report only; the Worker's own schedule decides when it runs) ---
$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -match 'Jarvis|Worker|Hub|Waker|Runner|Agent|Bridge' -and $_.TaskPath -notmatch '\\Microsoft\\' }
foreach ($t in $tasks) {
    $i = Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath $t.TaskPath -ErrorAction SilentlyContinue
    $last = if ($i -and $i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString('MM/dd HH:mm') } else { 'never' }
    $res  = if ($i) { '0x{0:X}' -f $i.LastTaskResult } else { '?' }
    $lines.Add("Task '$($t.TaskName)': $($t.State), last run $last, result $res")
}

# --- 4. Newest Worker/agent log tail --------------------------------------------------
$logRoots = @("$env:USERPROFILE\JarvisAgent\logs", 'C:\Jarvis\logs') | Where-Object { Test-Path $_ }
$newest = Get-ChildItem -Path $logRoots -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch 'remote-control|watchdog' } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($newest) {
    $lines.Add("Newest log: $($newest.FullName) ($($newest.LastWriteTime.ToString('MM/dd HH:mm')))")
    Get-Content $newest.FullName -Tail 8 -ErrorAction SilentlyContinue | ForEach-Object {
        $l = (Redact $_).Trim()
        if ($l) { $lines.Add('  ' + $l.Substring(0, [Math]::Min(180, $l.Length))) }
    }
}

# --- 5. Post to Notion ----------------------------------------------------------------
$snapshot = ($lines -join "`n")
if ($snapshot.Length -gt 1990) { $snapshot = $snapshot.Substring(0, 1990) }
$token = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'User')
if (-not $token) { $token = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'Machine') }
$card = $heartbeatCards[$machine]
if ($token -and $card) {
    $body = @{ properties = @{ 'Agent log' = @{ rich_text = @(@{ type = 'text'; text = @{ content = $snapshot } }) } } } |
        ConvertTo-Json -Depth 8
    try {
        Invoke-RestMethod -Method Patch -Uri "https://api.notion.com/v1/pages/$card" -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
            -ContentType 'application/json; charset=utf-8' -TimeoutSec 30 `
            -Headers @{ Authorization = "Bearer $token"; 'Notion-Version' = '2022-06-28' } | Out-Null
    } catch {
        Log "Notion heartbeat failed: $($_.Exception.Message)"
    }
} else {
    Log "No NOTION_TOKEN or unknown machine '$machine'; heartbeat not posted."
}
$snapshot | Set-Content -Path (Join-Path $logDir 'last-snapshot.txt') -Encoding UTF8
