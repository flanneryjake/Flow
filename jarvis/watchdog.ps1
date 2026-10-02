# Jarvis watchdog. Runs every 5 min as a scheduled task (installed by install.ps1).
#  1. Keeps `claude remote-control` alive, so phone/cloud Claude sessions can always reach this PC.
#  2. Rewrites this machine's pinned "Health: <machine>" issue in the GitHub tasks repo (Remote Control, Worker
#     state, last card claimed, approved cards waiting, full snapshot), so a cloud session (which can't get onto
#     Tailscale) and Jake's phone can see what this PC is doing and why a job is stuck.
# It never runs cards or anything else: its only actions are starting the Remote Control task, reading the
# tasks repo, and writing this machine's health issue. Notion is no longer read or written (retired 2026-10-02).

param(
    [switch]$DryRun,    # print the health update instead of sending it (no push either)
    [switch]$TestPush   # after the normal check, send one test notification to Jake's phone through the hub
)

$ErrorActionPreference = 'Continue'
$root    = $PSScriptRoot
# Plain string: in Windows PowerShell 5.1 a Get-Content line carries PSPath/PSProvider notes that ConvertTo-Json
# serializes in full, which bloated request bodies.
$machine = [string](Get-Content (Join-Path $root 'machine.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
$machine = $machine.Trim()
if (-not $machine) { $machine = $env:COMPUTERNAME }
$idleAfterMin = 15   # Worker counts as idle with cards waiting once nothing has been claimed for this long
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'watchdog.log'
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.old" -Force }
function Log([string]$m) { "$(Get-Date -Format 'MM/dd HH:mm:ss') $m" | Add-Content -Path $log }
# Notion, Anthropic, GitHub and Gemini keys, and JWTs such as Home Assistant long-lived tokens.
function Redact([string]$s) { $s -replace '(ntn_|secret_|sk-ant-|sk-|ghp_|github_pat_)[A-Za-z0-9_\-]{16,}|AIza[A-Za-z0-9_\-]{30,}|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+', '<redacted>' }

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("$(Get-Date -Format 'MM/dd HH:mm') $machine watchdog")

# GitHub tasks repo (cards, claims and the health issues). Token: GITHUB_TASKS_TOKEN user environment variable.
$ghTok  = @('User', 'Machine', 'Process') | ForEach-Object { [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', $_) } |
    Where-Object { $_ } | Select-Object -First 1
$ghRepo = @([Environment]::GetEnvironmentVariable('JARVIS_TASKS_REPO', 'User'), 'flanneryjake/jarvis-tasks') | Where-Object { $_ } | Select-Object -First 1
$ghHdr  = @{ Authorization = "Bearer $ghTok"; Accept = 'application/vnd.github+json'; 'User-Agent' = 'jarvis-watchdog' }
function Invoke-GH([string]$method, [string]$path, $body) {
    $a = @{ Method = $method; Uri = "https://api.github.com/repos/$ghRepo/$path"; Headers = $ghHdr; TimeoutSec = 30 }
    if ($null -ne $body) { $a.Body = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 8)); $a.ContentType = 'application/json; charset=utf-8' }
    Invoke-RestMethod @a
}
# This machine's (and on homebase, the rig's) "Health: <machine>" issue, label health.
# The list is piped through ForEach-Object because Windows PowerShell 5.1 hands a JSON array back as ONE object,
# which @() alone doesn't unroll. $healthRead stays false when the lookup fails, so a 403 (rate limit) or a timeout
# never makes this run file a second "Health: <machine>" issue (that's how the 5060 got #555 and #556 on 10/02).
$healthIssues = @()
$healthRead = $false
if ($ghTok) {
    try { $healthIssues = @(Invoke-GH Get 'issues?state=open&labels=health&per_page=100' | ForEach-Object { $_ }); $healthRead = $true }
    catch { Log "Reading the health issues failed: $(Redact $_.Exception.Message)" }
}
function Get-HealthIssue([string]$m) { $healthIssues | Where-Object { "$($_.title)" -match "^Health: $m(\s|$)" } | Sort-Object number | Select-Object -First 1 }
$myHealth = Get-HealthIssue $machine
# Remember our issue number so later runs keep writing the same one even if the list lookup fails.
$healthIdFile = Join-Path $PSScriptRoot 'health-issue.txt'
if (-not $myHealth -and $ghTok -and (Test-Path $healthIdFile)) {
    $savedId = "$(Get-Content $healthIdFile -TotalCount 1)".Trim()
    if ($savedId -match '^\d+$') {
        try {
            $i = Invoke-GH Get "issues/$savedId"
            if ($i.state -eq 'open' -and "$($i.title)" -match "^Health: $machine(\s|$)") { $myHealth = $i }
            $healthRead = $true
        } catch { Log "Reading health issue #$savedId failed: $(Redact $_.Exception.Message)" }
    }
}

# Remote kick: adding the label "restart-rc" to this machine's health issue (by Jake from the GitHub app, or by a
# cloud Claude session) makes this run restart Remote Control even if its process looks alive, e.g. when it is
# running but no longer connected. Section 6 removes the label again.
$kickLabel = 'restart-rc'
$kick = $myHealth -and @($myHealth.labels | ForEach-Object { $_.name }) -contains $kickLabel -and -not $DryRun

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
        $notes.Add("task start refused: $($_.Exception.Message.Trim()) (last result 0x$('{0:X8}' -f [uint32]$info.LastTaskResult))")
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
    if ($kick) { Log 'Restart requested from the health issue.'; $lines.Add('Remote Control: restart requested from GitHub') }
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
# Keep Remote Control ahead of heavy jobs (docker pulls in WSL, model runs) so a busy PC doesn't drop its
# connection: Remote Control and the sessions it spawns get AboveNormal priority. Memory goes on the snapshot,
# so a drop under memory pressure shows up on the row.
$memAlert = $null
$rcNow = Get-RcCopies $task
$rcIds = @(@($rcNow.task) + @($rcNow.hand) | ForEach-Object { [int]$_.ProcessId })
if ($rcIds -and -not $DryRun) {
    $rcTree = @($rcIds) + @(Get-Descendants $rcIds $rcNow.all | ForEach-Object { [int]$_.ProcessId })
    foreach ($id in ($rcTree | Select-Object -Unique)) {
        try { $p = Get-Process -Id $id -ErrorAction Stop; if ($p.PriorityClass -eq 'Normal') { $p.PriorityClass = 'AboveNormal' } } catch { }
    }
}
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $freeGb = [Math]::Round($os.FreePhysicalMemory / 1MB, 1); $totGb = [Math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    $wsl = @(Get-Process -Name vmmem, vmmemWSL -ErrorAction SilentlyContinue | Measure-Object WorkingSet64 -Sum).Sum
    $lines.Add("Memory: $freeGb GB free of $totGb GB" + $(if ($wsl) { ", WSL using $([Math]::Round($wsl / 1GB, 1)) GB" } else { '' }))
    if ($totGb -gt 0 -and $freeGb / $totGb -lt 0.07) { $memAlert = ("Low memory: $freeGb GB free of $totGb GB" + $(if ($wsl) { " (WSL $([Math]::Round($wsl / 1GB, 1)) GB)" } else { '' }) + '; Remote Control may drop') }
} catch { }
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
$ports = switch ($machine) {
    'rig'    { @{ 'ollama' = 11434 } }
    'laptop' { @{ 'ollama' = 11434 } }
    'backup' { @{ 'home assistant' = 8123; 'mqtt' = 1883; 'wake relay' = 8767 } }
    default  { @{ 'hub v2' = 8765; 'hub v3' = 8770; 'agent' = 8790; 'ollama' = 11434 } }
}
$portStatus = foreach ($k in $ports.Keys) {
    $up = Get-NetTCPConnection -State Listen -LocalPort $ports[$k] -ErrorAction SilentlyContinue
    "$k :$($ports[$k]) " + $(if ($up) { 'up' } else { 'DOWN' })
}
# Home Assistant and Mosquitto run in Docker inside WSL2, so Windows may not list their sockets (WSL mirrored
# networking); a real TCP connect to localhost is the reliable test.
function Test-Port([int]$port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { return ($c.ConnectAsync('127.0.0.1', $port).Wait(2000) -and $c.Connected) } catch { return $false } finally { $c.Close() }
}
if ($machine -eq 'homebase') {
    $portStatus = @($portStatus) + @(foreach ($p in @(@('home assistant', 8123), @('mqtt', 1883))) {
        "$($p[0]) :$($p[1]) " + $(if (Test-Port $p[1]) { 'up' } else { 'DOWN' })
    })
}
$lines.Add('Ports: ' + ($portStatus -join ', '))
if ($machine -eq 'homebase') {
    # When Home Assistant is down, show why: the tail of the HA stack installer/keeper log.
    if ($portStatus -match 'home assistant :8123 DOWN') {
        $haLog = Get-ChildItem -Path 'C:\Jarvis' -Recurse -Depth 3 -File -Include '*ha*stack*.log', '*install-ha*.log', '*homeassistant*.log' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($haLog) {
            $lines.Add("HA log: $($haLog.FullName) ($($haLog.LastWriteTime.ToString('MM/dd HH:mm')))")
            Get-Content $haLog.FullName -Tail 4 -ErrorAction SilentlyContinue | ForEach-Object {
                $l = (Redact $_).Trim()
                if ($l) { $lines.Add('  ' + $l.Substring(0, [Math]::Min(180, $l.Length))) }
            }
        } else { $lines.Add('HA log: none found under C:\Jarvis') }
    }
}

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
        # A reset a few minutes before the log line is today's (the Worker logs with a pad); roll to tomorrow only
        # when it is well in the past.
        if ($r -lt $at.AddHours(-2)) { $r = $r.AddDays(1) }
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
if ($memAlert) { $alerts.Add($memAlert) }
$waiting     = $null
$pausedUntil = $null
$claimLogAt  = $null
$statePath   = Join-Path $root 'state.json'
$state       = Get-Content $statePath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
$lastClaim   = if ($state -and $state.lastClaim) { [datetime]$state.lastClaim } else { $null }
$lastTitle   = if ($state) { "$($state.lastTitle)" } else { '' }

$waitingNames = @()

# Waiting cards come from the GitHub tasks repo: open, status:approved, unclaimed, for this machine or any.
# Claims come from the Worker's claim comments.
$waiting = $null
if (-not $ghTok) {
    $alerts.Add('GITHUB_TASKS_TOKEN is not set; waiting cards unknown and health not reported')
} elseif ($machine -ne 'backup') {  # the backup box runs no Worker, so it skips the queue calls
    try {
        # Only open approved cards are needed for the count (state=all paged up to 10 calls every run, which
        # mattered once the shared token started hitting GitHub's hourly limit).
        $issues = @(); $page = 1
        do {
            $batch = @(Invoke-RestMethod -Uri "https://api.github.com/repos/$ghRepo/issues?state=open&labels=status:approved&per_page=100&page=$page" -Headers $ghHdr -TimeoutSec 30 | ForEach-Object { $_ })
            $issues += $batch; $page++
        } while ($batch.Count -eq 100 -and $page -le 10)
        $issues = @($issues | Where-Object { -not $_.pull_request })
        $ghReady = @($issues | Where-Object {
            $n = @($_.labels | ForEach-Object { $_.name })
            -not @($n | Where-Object { $_ -like 'claimed:*' }) -and
                ($n -contains 'machine:any' -or $n -contains "machine:$machine" -or -not @($n | Where-Object { $_ -like 'machine:*' }))
        })
        $waiting = $ghReady.Count
        $waitingNames = @($ghReady | ForEach-Object { "#$($_.number) $($_.title)" })

        # Newest claim by this machine, and whether a run was logged after it. Look on the cards this machine
        # holds right now first (label claimed:<machine>); the repo-wide newest-100 comments are only a fallback,
        # because follow-up comments from all PCs push claims out of that window within minutes.
        $claimC = $null; $cm = @(); $claimIssue = $null
        $held = @(Invoke-RestMethod -Uri "https://api.github.com/repos/$ghRepo/issues?state=open&labels=claimed:$machine&sort=updated&direction=desc&per_page=5" -Headers $ghHdr -TimeoutSec 30 | ForEach-Object { $_ })
        if ($held.Count) {
            $claimIssue = $held[0]
            $cm = @(Invoke-RestMethod -Uri "https://api.github.com/repos/$ghRepo/issues/$($claimIssue.number)/comments?per_page=100" -Headers $ghHdr -TimeoutSec 30 | ForEach-Object { $_ })
            [array]::Reverse($cm)
            $claimC = $cm | Where-Object { "$($_.body)" -match "^<!-- jarvis:claim $machine " } | Select-Object -First 1
        }
        if (-not $claimC) {
            $cm = @(Invoke-RestMethod -Uri "https://api.github.com/repos/$ghRepo/issues/comments?sort=created&direction=desc&per_page=100" -Headers $ghHdr -TimeoutSec 30 | ForEach-Object { $_ })
            $claimC = $cm | Where-Object { "$($_.body)" -match "^<!-- jarvis:claim $machine " } | Select-Object -First 1
            $claimIssue = $null
        }
        if ($claimC) {
            $t = ([datetime]$claimC.created_at).ToLocalTime()
            if (-not $lastClaim -or $t -gt $lastClaim) {
                $lastClaim  = $t
                $num        = [int](($claimC.issue_url -split '/')[-1])
                $titleSrc   = if ($claimIssue) { $claimIssue } else { $issues | Where-Object { $_.number -eq $num } | Select-Object -First 1 }
                $lastTitle  = "#$num " + "$($titleSrc.title)"
                $claimLogAt = $null
                $runC = $cm | Where-Object { $_.issue_url -eq $claimC.issue_url -and "$($_.body)" -match "jarvis:runmeta \{[^}]*`"machine`": `"$machine`"" } | Select-Object -First 1
                if ($runC) { $claimLogAt = ([datetime]$runC.created_at).ToLocalTime() }
            }
        }
    } catch {
        $waiting = $null
        $alerts.Add('Could not read the GitHub task queue: ' + $_.Exception.Message)
        Log "GitHub queue query failed: $(Redact $_.Exception.Message)"
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

# The Worker's own heartbeat (homebase agent.py writes C:\Jarvis\worker.heartbeat): a worker line, "Working: ..." or
# "IDLE-REASON: <code> <detail>", and a rig line, "Rig: ...". When it is fresh it is the truth about the Worker,
# better than inferring idle from the card count.
$hbPath   = Join-Path (Split-Path $root -Parent) 'worker.heartbeat'
$hbWorker = $null
$hbRig    = $null
if ((Test-Path $hbPath) -and ($now - (Get-Item $hbPath).LastWriteTime).TotalMinutes -lt 20) {
    foreach ($l in @(Get-Content $hbPath -ErrorAction SilentlyContinue)) {
        $v = ($l -replace '^\s*(worker|rig)\s*[:=]\s*(?=(Working|IDLE-REASON|Rig)\b)', '').Trim()
        if (-not $hbWorker -and $v -match '^(Working|IDLE-REASON)\b') { $hbWorker = $v }
        elseif (-not $hbRig -and ($v -match '^Rig\b' -or $l -match '^\s*rig\s*[:=]')) { $hbRig = ($v -replace '^\s*rig\s*[:=]\s*', '') }
    }
}
$hbIdle = $null
if ($hbWorker -match '^Working\b') { $worker = 'Working' }
elseif ($hbWorker -match '^IDLE-REASON:?\s*(.*)$') {
    $hbIdle = $Matches[1].Trim()
    if ($hbIdle -match '(?i)usage|session limit|rate.?limit') { $worker = 'Paused (usage limit)' }
    elseif ($hbIdle -match '(?i)^(no[-_ ]?(cards|work)|queue[-_ ]?empty|nothing)') { $worker = 'Idle'; $hbIdle = $null }
    elseif ($worker -ne 'Paused (usage limit)') { $worker = 'Idle with cards waiting' }
}

if ($machine -eq 'backup') {
    # No Worker on the backup box (Home Assistant, Mosquitto, wake relay only): never report it idle or
    # waiting on Jake, or its health issue would carry an alert and the 5060 would push it to Jake's phone.
    $worker = 'backup (off)'; $hbWorker = $null; $hbIdle = $null; $needsJake = $null; $waiting = $null
}
$claimText = if ($lastClaim) { $lastClaim.ToString('MM/dd HH:mm') } else { 'never' }
$workerLine = "Worker: $worker"
if ($worker -eq 'Paused (usage limit)') { $workerLine += " until $($pausedUntil.ToString('MM/dd HH:mm'))" }
$workerLine += ", last claim $claimText"
if ($lastTitle) { $workerLine += " ($lastTitle)" }
$lines.Insert(1, $workerLine)
if ($hbWorker) { $lines.Insert(2, "  heartbeat: $hbWorker") }
if ($hbRig)    { $lines.Insert($(if ($hbWorker) { 3 } else { 2 }), "  Rig: $hbRig") }
if ($null -ne $waiting) {
    $names = @($waitingNames | Select-Object -First 3)
    $lines.Insert(2 + [int][bool]$hbWorker + [int][bool]$hbRig, "Approved cards waiting: $waiting" + $(if ($names) { ' (' + ($names -join '; ') + ')' } else { '' }))
}

if ($rcOutside) { $alerts.Add('Remote Control only running by hand (task copy not running); it stops if that terminal closes') }
if ($rcState -eq 'Down') { $alerts.Insert(0, "Remote Control down, restart failed: run 'claude remote-control' in C:\Jarvis") }
elseif ($rcState -ne 'Up') { $alerts.Insert(0, "Remote Control $($rcState.ToLower())") }
if ($hbIdle -and $worker -ne 'Paused (usage limit)') { $alerts.Insert(0, "Worker idle: $hbIdle") }
elseif (-not $hbWorker -and $worker -eq 'Idle with cards waiting') { $alerts.Insert(0, "Worker idle with $waiting approved card$(if ($waiting -ne 1) { 's' }) waiting, last claim $claimText") }
if ($needsJake) { $alerts.Insert(0, "Needs Jake: $needsJake") }
$portsDown = @($portStatus | Where-Object { $_ -like '*DOWN' })
if ($portsDown) { $alerts.Add('Down: ' + (($portsDown | ForEach-Object { ($_ -split ' :')[0] }) -join ', ')) }

# --- 6. Write this machine's health issue ---------------------------------------------
$snapshot = ($lines -join "`n")
$fields = [ordered]@{
    'Remote Control'    = $rcState
    'Worker'            = $worker
    'Waiting cards'     = $(if ($null -ne $waiting) { $waiting } else { 'unknown' })
    'Last claim'        = $claimText
    'Last claimed card' = $lastTitle
}
$body = @("**Last check-in:** $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss+00:00'))", '')
if ($alerts.Count) { $body += @('> [!WARNING]', ('> ' + ($alerts -join '; ')), '') }
$body += @('| | |', '|---|---|') + @($fields.Keys | ForEach-Object { "| $_ | $("$($fields[$_])".Replace('|', '/')) |" })
$tail5k = $snapshot.Trim(); if ($tail5k.Length -gt 5000) { $tail5k = $tail5k.Substring($tail5k.Length - 5000) }
$body += @('', '```', $tail5k.Replace('```', "'''"), '```')
$title = "Health: $machine" + $(if ($worker) { " - $worker" } else { '' })
$labels = @('health') + @($(if ($myHealth) { $myHealth.labels | ForEach-Object { $_.name } }) |
    Where-Object { $_ -notin @('health', 'health:alert', $kickLabel) }) + @($(if ($alerts.Count) { 'health:alert' }))
$update = @{ title = $title; body = ($body -join "`n"); labels = @($labels | Where-Object { $_ }) }
if ($DryRun) {
    $update | ConvertTo-Json -Depth 5
} elseif ($ghTok) {
    try {
        if ($myHealth) {
            Invoke-GH Patch "issues/$($myHealth.number)" $update | Out-Null
            Set-Content -Path $healthIdFile -Value $myHealth.number -Encoding ASCII
        } elseif ($healthRead) {
            $created = Invoke-GH Post 'issues' $update
            if ($created.number) { Set-Content -Path $healthIdFile -Value $created.number -Encoding ASCII }
        } else {
            Log 'Health issue lookup failed this run; not creating a new one.'
        }
    } catch { Log "GitHub health update failed: $(Redact $_.Exception.Message)" }
} else {
    Log 'No GITHUB_TASKS_TOKEN; health issue not updated.'
}
$snapshot | Set-Content -Path (Join-Path $logDir 'last-snapshot.txt') -Encoding UTF8

# --- 7. Phone alerts (homebase only; it reads every row) -------------------------------
# Pushes through the hub's /api/notify (web push to Jake's phone) when a row's alert changes to something new,
# and when the rig has been quiet for 30+ min while it has approved cards waiting (a sleeping idle rig is normal).
# "Remote Control restarted" alone is not pushed: the watchdog already fixed it.
$notifyUrls = @('http://127.0.0.1:8770/api/notify', 'http://127.0.0.1:8765/api/notify')
function Send-Push([string]$title, [string]$text) {
    $json = @{ title = $title; body = $text; message = $text; tag = 'jarvis-health'; url = "https://github.com/$ghRepo/issues?q=is%3Aopen+label%3Ahealth" } | ConvertTo-Json
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
if ($machine -eq 'homebase' -and $ghTok -and -not $DryRun) {
    $pushPath = Join-Path $root 'pushed.json'
    $pushed = @{}
    $prev = Get-Content $pushPath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($prev) { $prev.PSObject.Properties | ForEach-Object { $pushed[$_.Name] = "$($_.Value)" } }
    $current = @{ homebase = ($alerts -join '; ') }
    # The other machines can't push (only homebase runs the hub), so homebase reads their rows and pushes for them.
    foreach ($other in 'rig', 'backup') {
        try {
            $rb = "$((Get-HealthIssue $other).body)"
            if (-not $rb) { continue }
            $oAlert = if ($rb -match '(?m)^> (?!\[!WARNING\])(.+)$') { $Matches[1].Trim() } else { '' }
            $oSeen  = if ($rb -match '\*\*Last check-in:\*\* (\S+)') { $Matches[1] } else { $null }
            $oWait  = if ($rb -match '(?m)^\| Waiting cards \| (\d+) \|') { [int]$Matches[1] } else { 0 }
            $quiet  = $oSeen -and ($now - [datetime]$oSeen).TotalMinutes -gt 30
            if ($other -eq 'rig' -and $quiet -and $oWait -gt 0) {
                # a sleeping rig is normal; only a quiet rig with work waiting is worth a push
                $oAlert = "Offline since $(([datetime]$oSeen).ToString('MM/dd HH:mm')) with $oWait cards waiting"
            } elseif ($other -eq 'backup' -and $quiet) {
                # the backup runs Home Assistant, MQTT and the rig wake relay; it should never go quiet
                $oAlert = "Offline since $(([datetime]$oSeen).ToString('MM/dd HH:mm')) (Home Assistant, alarms and rig wake)"
            }
            $current[$other] = $oAlert
        } catch { Log "Reading the $other health issue failed: $($_.Exception.Message)" }
    }
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
