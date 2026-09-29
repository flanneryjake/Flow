# Jarvis watchdog. Runs every 5 min as a scheduled task (installed by install.ps1).
#  1. Keeps `claude remote-control` alive, so phone/cloud Claude sessions can always reach this PC.
#  2. Writes this machine's row in the Notion "Machine Health" table (Remote Control, Worker state, last card
#     claimed, approved cards waiting, full snapshot), so a cloud session (which can't get onto Tailscale)
#     and Jake's phone can see what this PC is doing and why a job is stuck. The table's Health column
#     turns red on its own when a machine stops checking in for 15 min.
# It never runs Notion cards or anything else: its only actions are starting the Remote Control task,
# reading the Tasks board, and writing this machine's health row.

param([switch]$DryRun)   # -DryRun: print the Notion update instead of sending it

$ErrorActionPreference = 'Continue'
$root    = $PSScriptRoot
# Plain string: in Windows PowerShell 5.1 a Get-Content line carries PSPath/PSProvider notes that ConvertTo-Json
# serializes in full, which made the Tasks query body too large for Notion (413).
$machine = [string](Get-Content (Join-Path $root 'machine.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
$machine = $machine.Trim()
if (-not $machine) { $machine = $env:COMPUTERNAME }
# Rows in the "🩺 Machine Health" database under Jarvis Command Center.
$healthRows = @{
    homebase = '3ea11c3639af816bbd70fd523fe28b81'
    rig      = '3ea11c3639af81b5af25cb91f2f88cdd'
}
$tasksDb      = '7c1c59e927644dfba461c88a67dbd32c'   # Notion Tasks board
$idleAfterMin = 15   # Worker counts as idle with cards waiting once nothing has been claimed for this long
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
    $rcState = 'Up'
    $lines.Add("Remote Control: UP (pid $(@($rcProc)[0].ProcessId))")
} else {
    $task = Get-ScheduledTask -TaskName 'Jarvis Remote Control' -ErrorAction SilentlyContinue
    if ($task) {
        Start-ScheduledTask -TaskName 'Jarvis Remote Control'
        Log 'Remote Control was down; started the task.'
        $rcState = 'Restarted'
        $lines.Add('Remote Control: WAS DOWN, restarted just now')
    } else {
        $rcState = 'Task missing'
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
$workerTaskRunning = $false
foreach ($t in $tasks) {
    if ($t.TaskName -match 'Worker|Runner' -and $t.State -eq 'Running') { $workerTaskRunning = $true }
    $i = Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath $t.TaskPath -ErrorAction SilentlyContinue
    $last = if ($i -and $i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString('MM/dd HH:mm') } else { 'never' }
    $res  = if ($i) { '0x{0:X}' -f $i.LastTaskResult } else { '?' }
    $lines.Add("Task '$($t.TaskName)': $($t.State), last run $last, result $res")
}

# --- 4. Newest Worker/agent log tail --------------------------------------------------
$logRoots = @("$env:USERPROFILE\JarvisAgent\logs", 'C:\Jarvis\logs') | Where-Object { Test-Path $_ }
$newest = if ($logRoots) { Get-ChildItem -Path $logRoots -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch 'remote-control|watchdog' } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1 }
$tail = @()
if ($newest) {
    $tail = @(Get-Content $newest.FullName -Tail 8 -ErrorAction SilentlyContinue)
    $lines.Add("Newest log: $($newest.FullName) ($($newest.LastWriteTime.ToString('MM/dd HH:mm')))")
    $tail | ForEach-Object {
        $l = (Redact $_).Trim()
        if ($l) { $lines.Add('  ' + $l.Substring(0, [Math]::Min(180, $l.Length))) }
    }
}


# --- 5. Worker vs. the Tasks board ---------------------------------------------------
# Stamps on the board carry no year ("homebase 09/29 15:09", "WAITING: usage resets 09/29 19:16"), local time.
function Parse-Stamp([string]$s, [datetime]$now) {
    if ($s -notmatch '(\d\d/\d\d \d\d:\d\d)') { return $null }
    try { $t = [datetime]::ParseExact($Matches[1], 'MM/dd HH:mm', [Globalization.CultureInfo]::InvariantCulture) } catch { return $null }
    $t = $t.AddYears($now.Year - $t.Year)
    if ($t -gt $now.AddDays(2)) { $t = $t.AddYears(-1) }
    return $t
}
function Plain($richText) { (@($richText) | ForEach-Object { $_.plain_text }) -join '' }

# Working > Paused > Idle with cards waiting > Idle. $lastClaim / $pausedUntil may be $null; $waiting is $null when unknown.
function Get-WorkerState([bool]$working, $pausedUntil, $waiting, $lastClaim, [datetime]$now, [int]$idleAfterMin) {
    if ($working) { return 'Working' }
    if ($pausedUntil -and $pausedUntil -gt $now) { return 'Paused (usage limit)' }
    if ($null -eq $waiting) { return 'Unknown' }
    if ($waiting -gt 0 -and (-not $lastClaim -or ($now - $lastClaim).TotalMinutes -ge $idleAfterMin)) { return 'Idle with cards waiting' }
    return 'Idle'
}

$token = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'User')
if (-not $token) { $token = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'Machine') }
if (-not $token) { $token = $env:NOTION_TOKEN }
$headers = @{ Authorization = "Bearer $token"; 'Notion-Version' = '2022-06-28' }
function Invoke-Notion([string]$method, [string]$path, $body) {
    $json = $body | ConvertTo-Json -Depth 12
    Invoke-RestMethod -Method $method -Uri "https://api.notion.com/v1/$path" -Headers $headers -TimeoutSec 30 `
        -Body ([Text.Encoding]::UTF8.GetBytes($json)) -ContentType 'application/json; charset=utf-8'
}

$now         = Get-Date
$alerts      = New-Object System.Collections.Generic.List[string]
$waiting     = $null
$pausedUntil = $null
$claimLogAt  = $null
$statePath   = Join-Path $root 'state.json'
$state       = Get-Content $statePath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
$lastClaim   = if ($state -and $state.lastClaim) { [datetime]$state.lastClaim } else { $null }
$lastTitle   = if ($state) { "$($state.lastTitle)" } else { '' }

if ($token) {
    try {
        # Cards the Worker could run right now: Approved, auto-executable, unclaimed, for this machine or Any.
        $q = Invoke-Notion Post "databases/$tasksDb/query" @{ page_size = 100; filter = @{ and = @(
            @{ property = 'Status'; select = @{ equals = 'Approved' } },
            @{ property = 'Auto-executable'; checkbox = @{ equals = $true } },
            @{ property = 'Claimed by'; rich_text = @{ is_empty = $true } },
            @{ or = @(@{ property = 'Machine'; select = @{ equals = $machine } }, @{ property = 'Machine'; select = @{ equals = 'Any' } }) }
        ) } }
        $waitingCards = @($q.results)
        $waiting = $waitingCards.Count

        # Newest claims by this machine (the Worker stamps "Claimed by" = "<machine> MM/dd HH:mm").
        $c = Invoke-Notion Post "databases/$tasksDb/query" @{ page_size = 25
            filter = @{ property = 'Claimed by'; rich_text = @{ starts_with = "$machine " } }
            sorts  = @(@{ timestamp = 'last_edited_time'; direction = 'descending' }) }
        foreach ($p in @($c.results)) {
            $t = Parse-Stamp (Plain $p.properties.'Claimed by'.rich_text) $now
            if ($t -and (-not $lastClaim -or $t -gt $lastClaim)) {
                $lastClaim  = $t
                $lastTitle  = Plain $p.properties.Task.title
                $claimLogAt = Parse-Stamp (Plain $p.properties.'Agent log'.rich_text) $now
            }
        }

        # A Worker that hit the usage limit writes "WAITING: usage resets MM/dd HH:mm" to the card.
        foreach ($p in @($waitingCards) + @($c.results)) {
            $log = Plain $p.properties.'Agent log'.rich_text
            if ($log -match 'usage resets (\d\d/\d\d \d\d:\d\d)') {
                $r = Parse-Stamp $Matches[1] $now
                if ($r -and $r -gt $now -and (-not $pausedUntil -or $r -gt $pausedUntil)) { $pausedUntil = $r }
            }
        }
    } catch {
        $alerts.Add('Could not read the Tasks board: ' + $_.Exception.Message)
        Log "Tasks query failed: $($_.Exception.Message)"
    }
}
foreach ($l in $tail) {
    if ($l -match 'usage resets (\d\d/\d\d \d\d:\d\d)') {
        $r = Parse-Stamp $Matches[1] $now
        if ($r -and $r -gt $now -and (-not $pausedUntil -or $r -gt $pausedUntil)) { $pausedUntil = $r }
    }
}
if ($lastClaim) {
    @{ lastClaim = $lastClaim.ToString('o'); lastTitle = $lastTitle } | ConvertTo-Json | Set-Content -Path $statePath -Encoding UTF8
}

# Working = the Worker task is running, a task log was written in the last 5 min, or the newest claim is
# under 50 min old (Worker timeout is 45) and the card has no result logged since the claim.
$recentTaskLog = $newest -and $newest.Name -like 'task-*' -and ($now - $newest.LastWriteTime).TotalMinutes -lt 5
$openClaim     = $lastClaim -and ($now - $lastClaim).TotalMinutes -lt 50 -and (-not $claimLogAt -or $claimLogAt -lt $lastClaim)
$worker = Get-WorkerState ($workerTaskRunning -or $recentTaskLog -or $openClaim) $pausedUntil $waiting $lastClaim $now $idleAfterMin

$claimText = if ($lastClaim) { $lastClaim.ToString('MM/dd HH:mm') } else { 'never' }
$workerLine = "Worker: $worker"
if ($worker -eq 'Paused (usage limit)') { $workerLine += " until $($pausedUntil.ToString('MM/dd HH:mm'))" }
$workerLine += ", last claim $claimText"
if ($lastTitle) { $workerLine += " ($lastTitle)" }
$lines.Insert(1, $workerLine)
if ($null -ne $waiting) {
    $names = @($waitingCards | Select-Object -First 3 | ForEach-Object { Plain $_.properties.Task.title })
    $lines.Insert(2, "Approved cards waiting: $waiting" + $(if ($names) { ' (' + ($names -join '; ') + ')' } else { '' }))
}

if ($rcState -ne 'Up') { $alerts.Insert(0, "Remote Control $($rcState.ToLower())") }
if ($worker -eq 'Idle with cards waiting') { $alerts.Insert(0, "Worker idle with $waiting approved card$(if ($waiting -ne 1) { 's' }) waiting, last claim $claimText") }
$portsDown = @($portStatus | Where-Object { $_ -like '*DOWN' })
if ($portsDown) { $alerts.Add('Down: ' + (($portsDown | ForEach-Object { ($_ -split ' :')[0] }) -join ', ')) }

# --- 6. Write this machine's health row -----------------------------------------------
function RT([string]$s) {
    if (-not $s) { return , @() }
    if ($s.Length -gt 1990) { $s = $s.Substring(0, 1990) }
    return , @(@{ type = 'text'; text = @{ content = $s } })
}
function NDate($d) { if ($d) { @{ start = $d.ToString('yyyy-MM-ddTHH:mm:sszzz') } } else { $null } }

$snapshot = ($lines -join "`n")
$props = [ordered]@{
    'Last check-in'     = @{ date = (NDate $now) }
    'Remote Control'    = @{ select = @{ name = $rcState } }
    'Worker'            = @{ select = @{ name = $worker } }
    'Waiting cards'     = @{ number = $waiting }
    'Last claim'        = @{ date = (NDate $lastClaim) }
    'Last claimed card' = @{ rich_text = (RT $lastTitle) }
    'Alert'             = @{ rich_text = (RT ($alerts -join '; ')) }
    'Snapshot'          = @{ rich_text = (RT $snapshot) }
}
$row = $healthRows[$machine]
if ($DryRun) {
    @{ row = $row; properties = $props } | ConvertTo-Json -Depth 12
} elseif ($token -and $row) {
    try { Invoke-Notion Patch "pages/$row" @{ properties = $props } | Out-Null }
    catch { Log "Notion health update failed: $($_.Exception.Message)" }
} else {
    Log "No NOTION_TOKEN or unknown machine '$machine'; health row not updated."
}
$snapshot | Set-Content -Path (Join-Path $logDir 'last-snapshot.txt') -Encoding UTF8
