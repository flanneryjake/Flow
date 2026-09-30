# Jarvis watchdog. Runs every 5 min as a scheduled task (installed by install.ps1).
#  1. Keeps `claude remote-control` alive, so phone/cloud Claude sessions can always reach this PC.
#  2. Writes this machine's row in the Notion "Machine Health" table (Remote Control, Worker state, last card
#     claimed, approved cards waiting, full snapshot), so a cloud session (which can't get onto Tailscale)
#     and Jake's phone can see what this PC is doing and why a job is stuck. The table's Health column
#     turns red on its own when a machine stops checking in for 15 min.
# It never runs Notion cards or anything else: its only actions are starting the Remote Control task,
# reading the Tasks board, and writing this machine's health row.

param(
    [switch]$DryRun,    # print the Notion update instead of sending it (no push either)
    [switch]$TestPush   # after the normal check, send one test notification to Jake's phone through the hub
)

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
function Redact([string]$s) { $s -replace '(ntn_|secret_|sk-ant-|sk-|ghp_|github_pat_)[A-Za-z0-9_\-]{16,}', '<redacted>' }

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("$(Get-Date -Format 'MM/dd HH:mm') $machine watchdog")

$token = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'User')
if (-not $token) { $token = [Environment]::GetEnvironmentVariable('NOTION_TOKEN', 'Machine') }
if (-not $token) { $token = $env:NOTION_TOKEN }
$headers = @{ Authorization = "Bearer $token"; 'Notion-Version' = '2022-06-28' }
function Invoke-Notion([string]$method, [string]$path, $body) {
    $json = $body | ConvertTo-Json -Depth 12
    Invoke-RestMethod -Method $method -Uri "https://api.notion.com/v1/$path" -Headers $headers -TimeoutSec 30 `
        -Body ([Text.Encoding]::UTF8.GetBytes($json)) -ContentType 'application/json; charset=utf-8'
}

# Remote kick: ticking "Restart Remote Control" on this machine's Machine Health row (by Jake, or by a cloud Claude
# session through Notion) makes this run restart Remote Control even if its process looks alive, e.g. when it is
# running but no longer connected. Section 6 unticks it again.
$kickProp = 'Restart Remote Control'
$kickHas  = $false
$kick     = $false
if ($token -and $healthRows[$machine]) {
    try {
        $me = Invoke-RestMethod -Method Get -Uri "https://api.notion.com/v1/pages/$($healthRows[$machine])" -Headers $headers -TimeoutSec 30
        if ($me.properties.PSObject.Properties.Name -contains $kickProp) {
            $kickHas = $true
            $kick = [bool]$me.properties.$kickProp.checkbox -and -not $DryRun
        }
    } catch { Log "Reading the health row for a restart request failed: $($_.Exception.Message)" }
}

# --- 1. Remote Control ---------------------------------------------------------------
# Two kinds of copy can be running:
#  - the task's copy. On homebase the task runs as S4U, so it lives in session 0, and this watchdog (not elevated)
#    can't read its command line. It is found through Task Scheduler instead: the running task's engine PID (its
#    powershell wrapper) and any claude.exe under it.
#  - a copy typed into a terminal by hand, found by its command line (`claude remote-control`, whatever the exe name).
$rcTaskName = 'Jarvis Remote Control'
$wrappers = 'powershell.exe', 'pwsh.exe', 'cmd.exe', 'conhost.exe'
function Get-Descendants($rootIds, $all) {
    $found = New-Object System.Collections.Generic.List[object]
    $queue = New-Object System.Collections.Generic.Queue[int]
    foreach ($r in $rootIds) { $queue.Enqueue([int]$r) }
    while ($queue.Count -gt 0) {
        $id = $queue.Dequeue()
        foreach ($c in @($all | Where-Object { $_.ParentProcessId -eq $id -and $_.ProcessId -ne $id })) {
            if ($found.ProcessId -notcontains $c.ProcessId) { $found.Add($c); $queue.Enqueue([int]$c.ProcessId) }
        }
    }
    return , $found
}
function Get-TaskEnginePids($task) {
    try {
        $svc = New-Object -ComObject Schedule.Service
        $svc.Connect()
        $path = $task.TaskPath + $task.TaskName
        return @($svc.GetRunningTasks(1) | Where-Object { $_.Path -eq $path } | ForEach-Object { [int]$_.EnginePID } | Where-Object { $_ })
    } catch {
        # Fallback: a session-0 powershell started by Task Scheduler's svchost. Never guess "nothing is running",
        # since that would make the watchdog stop a working copy.
        $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
        $svch = @($all | Where-Object { $_.Name -ieq 'svchost.exe' } | ForEach-Object { $_.ProcessId })
        return @($all | Where-Object { $_.SessionId -eq 0 -and $wrappers -contains "$($_.Name)".ToLower() -and $svch -contains $_.ParentProcessId } | ForEach-Object { [int]$_.ProcessId })
    }
}
# Returns @{ task = claude.exe processes under the running task; hand = hand-started copies; engine = wrapper PIDs }
function Get-RcCopies($task) {
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $engine = if ($task) { Get-TaskEnginePids $task } else { @() }
    $under = if ($engine) { Get-Descendants $engine $all } else { @() }
    $taskRc = @($under | Where-Object { $_.Name -ieq 'claude.exe' -or $_.Name -ieq 'node.exe' })
    $underIds = @($under | ForEach-Object { $_.ProcessId }) + $engine
    # A hand copy's own child claude.exe (sessions it spawns) carries no remote-control in its command line, so
    # counting only command-line matches keeps a hand copy to one entry per copy.
    $hand = @($all | Where-Object {
        $_.CommandLine -match '\bremote-control\b' -and $wrappers -notcontains "$($_.Name)".ToLower() -and
        $_.ProcessId -ne $PID -and $underIds -notcontains $_.ProcessId
    })
    # A copy this watchdog started directly sits under a visible wrapper whose command line carries remote-control;
    # that counts as the task's command, not a hand copy.
    $hand = @($hand | Where-Object {
        $pp = $_.ParentProcessId
        -not ($all | Where-Object { $_.ProcessId -eq $pp -and $wrappers -contains "$($_.Name)".ToLower() -and $_.CommandLine -match '\bremote-control\b' })
    })
    $direct = @($all | Where-Object {
        $_.CommandLine -match '\bremote-control\b' -and $wrappers -notcontains "$($_.Name)".ToLower() -and
        $_.ProcessId -ne $PID -and $underIds -notcontains $_.ProcessId -and $hand.ProcessId -notcontains $_.ProcessId
    })
    return @{ task = @($taskRc) + $direct; hand = $hand; engine = $engine; all = $all }
}
function Test-RcUp($task) { $c = Get-RcCopies $task; return [bool]($c.task -or $c.hand) }

# Restart order, each step only if the one before didn't bring Remote Control back:
#  1. Stop the task run if Task Scheduler still holds one with no claude under it (a plain start is refused with
#     0x800710E0 while a run is "going", because the task won't start a second instance), then start the task.
#  2. Start the task's own command directly from this watchdog run (same user, hidden window).
function Restart-RemoteControl($task, [bool]$force) {
    $notes = New-Object System.Collections.Generic.List[string]
    $c = Get-RcCopies $task
    $task = Get-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction SilentlyContinue
    if ($task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction SilentlyContinue
        $notes.Add($(if ($force) { 'stopped the task run' } else { 'stopped a task run with no Remote Control under it' }))
    }
    # Stop-ScheduledTask ends the wrapper; make sure nothing it started is left holding the registration.
    foreach ($p in @($c.task)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    if ($force) { foreach ($p in @($c.hand)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; $notes.Add("stopped hand-started copy pid $($p.ProcessId)") } }
    Start-Sleep -Seconds 2
    try {
        Start-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop
        $notes.Add('started the task')
    } catch {
        $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction SilentlyContinue
        $notes.Add("task start refused: $($_.Exception.Message.Trim()) (last result 0x$('{0:X8}' -f [int]$info.LastTaskResult))")
    }
    for ($i = 0; $i -lt 6 -and -not (Test-RcUp $task); $i++) { Start-Sleep -Seconds 5 }
    if (Test-RcUp $task) { return @{ ok = $true; notes = $notes } }

    $a = @($task.Actions)[0]
    if ($a -and $a.Execute) {
        try {
            $sp = @{ FilePath = $a.Execute; WindowStyle = 'Hidden'; ErrorAction = 'Stop' }
            if ($a.Arguments)        { $sp.ArgumentList = $a.Arguments }
            if ($a.WorkingDirectory) { $sp.WorkingDirectory = $a.WorkingDirectory }
            Start-Process @sp
            $notes.Add('started the task command directly')
        } catch { $notes.Add("direct start failed: $($_.Exception.Message.Trim())") }
        for ($i = 0; $i -lt 6 -and -not (Test-RcUp $task); $i++) { Start-Sleep -Seconds 5 }
        if (Test-RcUp $task) { return @{ ok = $true; notes = $notes } }
    }
    return @{ ok = $false; notes = $notes }
}

$rcOutside = $false
$task = Get-ScheduledTask -TaskName $rcTaskName -ErrorAction SilentlyContinue
$copies = Get-RcCopies $task
if (-not $task) {
    $rcState = if ($copies.hand) { 'Up' } else { 'Task missing' }
    $lines.Add('Remote Control: task missing (re-run install.ps1)' + $(if ($copies.hand) { ', running by hand' } else { '' }))
} elseif ($kick -or -not ($copies.task -or $copies.hand)) {
    if ($kick) { Log 'Restart requested from the Machine Health row.'; $lines.Add('Remote Control: restart requested from Notion') }
    $r = Restart-RemoteControl $task $kick
    $how = $r.notes -join '; '
    if ($r.ok) {
        $rcState = 'Restarted'
        Log "Remote Control restarted ($how)."
        $lines.Add("Remote Control: restarted just now ($how)")
    } else {
        $rcState = 'Down'   # existing red option in the Machine Health select
        Log "Remote Control was down and did NOT come back: $how"
        $lines.Add("Remote Control: DOWN, restart failed ($how). Run 'claude remote-control' in C:\Jarvis by hand.")
    }
} else {
    $rcState = 'Up'
    $desc = @()
    if ($copies.task) { $desc += "task copy pid $(@($copies.task)[0].ProcessId)" }
    if ($copies.hand) { $desc += "hand-started pid $(@($copies.hand.ProcessId) -join ', ')" }
    $lines.Add("Remote Control: UP ($($desc -join '; '))")
    if (-not $copies.task) {
        # Only a hand copy: it dies with its terminal, so bring the task's copy back alongside it (never stop it).
        $r = Restart-RemoteControl $task $false
        $after = Get-RcCopies $task
        if ($after.task) { $lines.Add("  also started the task's copy ($($r.notes -join '; '))"); Log "Only a hand-started Remote Control was up; started the task's copy too." }
        else { $rcOutside = $true }
    }
}
# Remote Control's own output: the homebase task redirects it to C:\Jarvis\logs\remote-control.log; installs from
# install.ps1 write a --debug-file into this folder.
$rcDebug = @((Join-Path (Split-Path $root -Parent) 'logs\remote-control.log'), (Join-Path $logDir 'remote-control-debug.log')) |
    Where-Object { Test-Path $_ } | Sort-Object { (Get-Item $_).LastWriteTime } -Descending | Select-Object -First 1
if (-not $rcDebug) { $rcDebug = Join-Path $logDir 'remote-control-debug.log' }
if ($rcState -eq 'Down' -and (Test-Path $rcDebug)) {
    # Whatever Remote Control last wrote before it quit, so the reason is on the row without opening the PC.
    $lw = (Get-Item $rcDebug).LastWriteTime
    $lines.Add("  RC debug log last written $($lw.ToString('MM/dd HH:mm')), tail:")
    Get-Content $rcDebug -Tail 4 -ErrorAction SilentlyContinue | ForEach-Object {
        $l = (Redact $_).Trim(); $lines.Add('    ' + $l.Substring(0, [Math]::Min(200, $l.Length)))
    }
} elseif (Test-Path $rcDebug) {
    # Real problems only: skip the --verbose websocket traffic (it carries "is_error" etc.) and anything older than 30 min.
    $bad = Get-Content $rcDebug -Tail 300 -ErrorAction SilentlyContinue | Select-String -Pattern 'not trusted|requires a claude.ai|full-scope|not yet enabled|Enable Remote Control\?|trusted-device|could not|\[ERROR\]|failed' |
        Where-Object { $_.Line -notmatch '\[bridge:ws\]|Error log sink' } |
        Where-Object { -not ($_.Line -match '^(\d{4}-\d\d-\d\dT[\d:.]+Z)') -or ((Get-Date) - [datetime]$Matches[1]).TotalMinutes -lt 30 } |
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
# The Worker's own log, for "needs Jake" and usage-limit lines (the newest log can be another script's, e.g. printer-send.log).
$agentLog = Join-Path $env:USERPROFILE 'JarvisAgent\logs\agent.log'
$agentLines = @(Get-Content $agentLog -Tail 300 -ErrorAction SilentlyContinue)
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

# Usage-limit reset time from a card or log line: "usage resets 09/29 19:16", or an agent.log line
# "2026-09-29 18:49:39 [rig] ... resets 7:10pm (America/New_York)" (dated from the line, next day if already past).
function Parse-ResetTime([string]$l, [datetime]$now) {
    if ($l -match 'usage resets (\d\d/\d\d \d\d:\d\d)') { return Parse-Stamp $Matches[1] $now }
    if ($l -match '^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d).*\bresets (\d{1,2})(?::(\d\d))?\s*([ap]m)') {
        $at = [datetime]::ParseExact("$($Matches[1]) $($Matches[2])", 'yyyy-MM-dd HH:mm:ss', [Globalization.CultureInfo]::InvariantCulture)
        $h = [int]$Matches[3] % 12; if ($Matches[5] -eq 'pm') { $h += 12 }
        $m = if ($Matches[4]) { [int]$Matches[4] } else { 0 }
        $r = $at.Date.AddHours($h).AddMinutes($m)
        if ($r -lt $at) { $r = $r.AddDays(1) }
        return $r
    }
    return $null
}

# Newest "needs Jake: ..." line the Worker logged in the last 12 h, or $null.
function Get-NeedsJake($logLines, [datetime]$now) {
    for ($i = @($logLines).Count - 1; $i -ge 0; $i--) {
        $l = "$(@($logLines)[$i])"
        if ($l -match '^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) .*?needs Jake:\s*(.+)$') {
            $at = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', [Globalization.CultureInfo]::InvariantCulture)
            if (($now - $at).TotalHours -gt 12) { return $null }
            $t = $Matches[2].Trim()
            if ($t.Length -gt 160) { $t = $t.Substring(0, 160) + '...' }
            return $t
        }
    }
    return $null
}

# Working > Paused > Idle with cards waiting > Idle. $lastClaim / $pausedUntil may be $null; $waiting is $null when unknown.
function Get-WorkerState([bool]$working, $pausedUntil, $waiting, $lastClaim, [datetime]$now, [int]$idleAfterMin) {
    if ($working) { return 'Working' }
    if ($pausedUntil -and $pausedUntil -gt $now) { return 'Paused (usage limit)' }
    if ($null -eq $waiting) { return 'Unknown' }
    if ($waiting -gt 0 -and (-not $lastClaim -or ($now - $lastClaim).TotalMinutes -ge $idleAfterMin)) { return 'Idle with cards waiting' }
    return 'Idle'
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
foreach ($l in @($tail) + @($agentLines)) {
    $r = Parse-ResetTime $l $now
    if ($r -and $r -gt $now -and (-not $pausedUntil -or $r -gt $pausedUntil)) { $pausedUntil = $r }
}
$needsJake = Get-NeedsJake $agentLines $now
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

if ($rcOutside) { $alerts.Add('Remote Control only running by hand (task copy not running); it stops if that terminal closes') }
if ($rcState -eq 'Down') { $alerts.Insert(0, "Remote Control down, restart failed: run 'claude remote-control' in C:\Jarvis") }
elseif ($rcState -ne 'Up') { $alerts.Insert(0, "Remote Control $($rcState.ToLower())") }
if ($worker -eq 'Idle with cards waiting') { $alerts.Insert(0, "Worker idle with $waiting approved card$(if ($waiting -ne 1) { 's' }) waiting, last claim $claimText") }
if ($needsJake) { $alerts.Insert(0, "Needs Jake: $needsJake") }
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
if ($kickHas) { $props[$kickProp] = @{ checkbox = $false } }
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

# Same row on GitHub (the pinned "Health: <machine>" issue in the tasks repo), while Notion is phased out.
# Runs only once install-ghq.ps1 has set GITHUB_TASKS_TOKEN.
$ghq = 'C:\Jarvis\ghq\ghq.py'
$ghToken = [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User')
$py = Get-Command python, py -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ghToken -and (Test-Path $ghq) -and $py -and -not $DryRun) {
    $healthJson = Join-Path $logDir 'health.json'
    @{
        fields   = [ordered]@{
            'Remote Control'    = $rcState
            'Worker'            = $worker
            'Waiting cards'     = $(if ($null -ne $waiting) { $waiting } else { 'unknown' })
            'Last claim'        = $claimText
            'Last claimed card' = $lastTitle
        }
        alerts   = @($alerts)
        snapshot = $snapshot
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $healthJson -Encoding UTF8
    $env:GITHUB_TASKS_TOKEN = $ghToken
    $out = & $py.Source $ghq health --machine $machine --json $healthJson 2>&1
    if ($LASTEXITCODE -ne 0) { Log "GitHub health update failed: $(Redact "$out")" }
}

# --- 7. Phone alerts (homebase only; it reads both rows) -------------------------------
# Pushes through the hub's /api/notify (web push to Jake's phone) when a row's alert changes to something new,
# and when the rig has been quiet for 30+ min while it has approved cards waiting (a sleeping idle rig is normal).
# "Remote Control restarted" alone is not pushed: the watchdog already fixed it.
$notifyUrls = @('http://127.0.0.1:8770/api/notify', 'http://127.0.0.1:8765/api/notify')
function Send-Push([string]$title, [string]$text) {
    $json = @{ title = $title; body = $text; message = $text; tag = 'jarvis-health'; url = 'https://app.notion.com/p/2e1df4a5efd6402689c5e67ee1691954' } | ConvertTo-Json
    foreach ($u in $notifyUrls) {
        try {
            Invoke-RestMethod -Method Post -Uri $u -Body ([Text.Encoding]::UTF8.GetBytes($json)) -ContentType 'application/json; charset=utf-8' -TimeoutSec 10 | Out-Null
            Log "Push sent via $u : $title - $text"
            return $true
        } catch { Log "Push via $u failed: $($_.Exception.Message)" }
    }
    return $false
}
if ($TestPush) {
    if (Send-Push 'Jarvis test' "Test push from the $machine watchdog") { 'Push sent.' } else { "Push failed; see $log" }
    return
}
if ($machine -eq 'homebase' -and $token -and -not $DryRun) {
    $pushPath = Join-Path $root 'pushed.json'
    $pushed = @{}
    $prev = Get-Content $pushPath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($prev) { $prev.PSObject.Properties | ForEach-Object { $pushed[$_.Name] = "$($_.Value)" } }
    $current = @{ homebase = ($alerts -join '; ') }
    try {
        $rp = Invoke-RestMethod -Method Get -Uri "https://api.notion.com/v1/pages/$($healthRows['rig'])" -Headers $headers -TimeoutSec 30
        $rigAlert = Plain $rp.properties.Alert.rich_text
        $rigSeen  = $rp.properties.'Last check-in'.date.start
        $rigWait  = $rp.properties.'Waiting cards'.number
        if ($rigSeen -and ($now - [datetime]$rigSeen).TotalMinutes -gt 30 -and $rigWait -gt 0) {
            $rigAlert = "Offline since $(([datetime]$rigSeen).ToString('MM/dd HH:mm')) with $rigWait cards waiting"
        }
        $current['rig'] = $rigAlert
    } catch { Log "Reading the rig row failed: $($_.Exception.Message)" }
    foreach ($m in @($current.Keys)) {
        $a = "$($current[$m])"
        $worth = ($a -split '; ' | Where-Object { $_ -and $_ -ne 'Remote Control restarted' })
        $sig = $a -replace '\d+', '#'   # a changing count or time alone doesn't re-push
        if ($worth -and $sig -ne $pushed[$m]) {
            if (Send-Push "Jarvis: $m" (($worth -join '; '))) { $pushed[$m] = $sig }
        } elseif (-not $worth) { $pushed[$m] = '' }
    }
    $pushed | ConvertTo-Json | Set-Content -Path $pushPath -Encoding UTF8
}
