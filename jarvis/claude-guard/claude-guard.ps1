# Jarvis Claude guard. Runs every 5 min as the "Jarvis Claude Guard" scheduled task (install-claude-guard.ps1),
# elevated so it can read every process's command line (the 5060 runs its Jarvis services elevated).
#
# On 10/02 the 5060 ended up with 13 Claude app processes and 8 Claude Code sessions, which ate its 16 GB and
# dropped Remote Control. This keeps count of every Claude process on the PC, cleans up the ones that are safe
# to remove, and tells Jake when a count goes over its cap.
#
# Each run:
#  1. Sorts every Claude process into one role:
#       listener      `claude remote-control` (the Remote Control server; one per PC)
#       rc-session    a session a listener spawned for a project thread or a phone/cloud session
#       headless      `claude -p` runs (Worker cards, agent.py, scripts)
#       desktop-code  a Claude Code session the Claude desktop app started (its Code tab)
#       interactive   `claude` typed into a terminal
#       desktop       the Claude desktop app (Electron: one main process plus ~10 helpers per window)
#     A Claude Code process under another one (sub-processes) is counted with its parent, not on its own.
#  2. Cleans up, never touching anything that can be doing real work:
#       - a headless run whose parent is gone (its Worker timed out or died), or older than 75 min
#         (the Worker's own limit is 45 min, with a 15 min no-progress timeout);
#       - an rc-session or desktop-code session whose listener / desktop app is gone, once it has used no CPU
#         for two runs in a row (a disconnected session nobody can reach any more);
#       - a duplicate listener with NO live sessions under it, seen on two runs in a row. A listener serving
#         sessions is never stopped, and the Remote Control task's own copy is the one kept.
#     Interactive sessions, the desktop app and anything with live work are reported, never stopped.
#  3. Writes C:\Jarvis\claude-guard\status.json and history.csv, and this PC's line on the pinned
#     "Fleet control" issue (the phone app's System > Fleet panel shows it).
#  4. Over a cap: pushes to Jake's phone and comments on this PC's Health issue with the process list. Once per
#     new problem, again every 3 h while it lasts, and one "back to normal" comment when it clears.
#
# Caps are in config.json next to this file (defaults below); a "machines" block can set them per PC.

param(
    [switch]$DryRun,          # decide and report, but stop nothing and write nothing to GitHub or the phone
    [switch]$TestPush,        # send one test notification and exit
    [string]$ProcessJson,     # tests: read the process list from this JSON file instead of Windows (implies -DryRun)
    [string]$StateDir,        # tests: keep state here instead of next to the script
    [string]$Now,             # tests: pretend it is this time (ISO 8601)
    [string]$Machine          # override the machine name
)

$ErrorActionPreference = 'Continue'
$root = if ($StateDir) { $StateDir } else { $PSScriptRoot }
New-Item -ItemType Directory -Force -Path $root | Out-Null
$testMode = [bool]$ProcessJson
if ($testMode) { $DryRun = $true }
$nowT = if ($Now) { [datetime]::Parse($Now, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal) } else { (Get-Date).ToUniversalTime() }

$log = Join-Path $root 'guard.log'
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.old" -Force }
function Log([string]$m) { if (-not $testMode) { "$(Get-Date -Format 'MM/dd HH:mm:ss') $m" | Add-Content -Path $log } }
function Redact([string]$s) { $s -replace '(ntn_|secret_|sk-ant-|sk-|ghp_|gho_|github_pat_)[A-Za-z0-9_\-]{16,}|AIza[A-Za-z0-9_\-]{30,}|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+', '<redacted>' }

# --- Which PC ---------------------------------------------------------------------------
if (-not $Machine) {
    switch ("$env:COMPUTERNAME".ToUpper()) {
        'LAPTOP-4150EGRS' { $Machine = 'homebase' }
        'DESKTOP-VLLDDM4' { $Machine = 'rig' }
        'DESKTOP-5VE3C77' { $Machine = 'backup' }
        default {
            $Machine = "$(Get-Content 'C:\Jarvis\watchdog\machine.txt' -TotalCount 1 -ErrorAction SilentlyContinue)".Trim()
            if (-not $Machine) { $Machine = "$env:COMPUTERNAME".ToLower() }
        }
    }
}

# --- Caps -------------------------------------------------------------------------------
$caps = [ordered]@{
    listeners           = 1    # one Remote Control server per PC
    rc_sessions         = 4    # threads/phone sessions running on this PC at once
    headless            = 2    # claude -p runs at once (Worker + one script)
    desktop_code        = 3
    interactive         = 2
    sessions_total      = 8    # every Claude Code session of any kind
    desktop_instances   = 1    # Claude desktop app windows (main processes)
    claude_mem_pct      = 35   # all Claude processes together, as a % of this PC's RAM
}
$headlessMaxMin = 75
$repeatHours = 3
$cfgPath = Join-Path $PSScriptRoot 'config.json'
$cfg = $null
if (Test-Path $cfgPath) {
    try { $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch { Log "config.json unreadable: $($_.Exception.Message)" }
}
function Apply-Caps($o) {
    if (-not $o) { return }
    foreach ($p in $o.PSObject.Properties) {
        if ($caps.Contains($p.Name)) { $caps[$p.Name] = [double]$p.Value }
        elseif ($p.Name -eq 'headless_max_minutes') { $script:headlessMaxMin = [double]$p.Value }
    }
}
if ($cfg) {
    Apply-Caps $cfg.caps
    if ($cfg.machines -and $cfg.machines.$Machine) { Apply-Caps $cfg.machines.$Machine }
}

# --- Process list -------------------------------------------------------------------------
# Plain objects: pid, ppid, name, path, cmd, created (UTC), mb, cpu (seconds), session.
function Get-ProcList {
    if ($testMode) {
        return @(Get-Content $ProcessJson -Raw | ConvertFrom-Json | ForEach-Object {
            [pscustomobject]@{
                pid = [int]$_.pid; ppid = [int]$_.ppid; name = "$($_.name)"; path = "$($_.path)"; cmd = "$($_.cmd)"
                created = [datetime]::Parse("$($_.created)", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal)
                mb = [double]$_.mb; cpu = [double]$_.cpu; session = [int]$_.session
            }
        })
    }
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        [pscustomobject]@{
            pid = [int]$_.ProcessId; ppid = [int]$_.ParentProcessId; name = "$($_.Name)"; path = "$($_.ExecutablePath)"
            cmd = "$($_.CommandLine)"
            created = $(if ($_.CreationDate) { $_.CreationDate.ToUniversalTime() } else { [datetime]::MinValue })
            mb = [Math]::Round($_.WorkingSetSize / 1MB, 1)
            cpu = [Math]::Round(([double]$_.KernelModeTime + [double]$_.UserModeTime) / 1e7, 1)
            session = [int]$_.SessionId
        }
    })
}

function Test-Desktop($p) {
    if ($p.name -notmatch '^claude(\.exe)?$') { return $false }
    # The desktop app installs under %LOCALAPPDATA%\AnthropicClaude (Squirrel) or WindowsApps (MSIX); its
    # Electron helpers carry --type=. The Claude Code binary the app downloads for its Code tab lives elsewhere
    # (e.g. %APPDATA%\Claude\claude-code), so it counts as Claude Code.
    return ($p.path -match '\\AnthropicClaude\\|\\WindowsApps\\[^\\]*Claude|\\Programs\\Claude\\' -or $p.cmd -match '\s--type=')
}
function Test-Cli($p) {
    if (Test-Desktop $p) { return $false }
    if ($p.name -match '^claude(\.exe)?$') { return $true }
    return ($p.name -match '^(node|bun)(\.exe)?$' -and $p.cmd -match '@anthropic-ai[\\/]claude-code|claude-code[\\/]cli\.(m?js)')
}

$procs = Get-ProcList
$byPid = @{}
foreach ($p in $procs) { $byPid[$p.pid] = $p }
# A parent only counts if it is still running and is older than the child (Windows reuses PIDs).
function Get-Parent($p) {
    $pp = $byPid[$p.ppid]
    if ($pp -and $pp.pid -ne $p.pid -and $pp.created -le $p.created) { return $pp }
    return $null
}
function Get-Ancestors($p) {
    $out = New-Object System.Collections.Generic.List[object]
    $cur = $p
    for ($i = 0; $i -lt 40; $i++) {
        $cur = Get-Parent $cur
        if (-not $cur) { break }
        $out.Add($cur)
    }
    return , $out
}

# The Remote Control task's own copy: anything under the task's engine process (Task Scheduler knows it even when
# the task runs in another session).
$taskEngine = @()
if (-not $testMode) {
    try {
        $svc = New-Object -ComObject Schedule.Service
        $svc.Connect()
        $taskEngine = @($svc.GetRunningTasks(1) | Where-Object { $_.Path -eq '\Jarvis Remote Control' } | ForEach-Object { [int]$_.EnginePID } | Where-Object { $_ })
    } catch { }
} elseif ($env:GUARD_TEST_TASK_ENGINE) { $taskEngine = @([int]$env:GUARD_TEST_TASK_ENGINE) }

# Processes that have a Claude Code child (a launcher, or a session running claude itself).
$cliParents = @{}
foreach ($p in $procs) { if ((Test-Cli $p) -and $byPid[$p.ppid]) { $cliParents[$p.ppid] = $true } }
$printRx = '(^|\s)(-p|--print)(\s|$)'
$bridgeRx = '--sdk-url|--input-format[ =]stream-json|--remote-control-session|--session-ingress'
# The `remote-control` subcommand itself, not a flag that contains the word (sessions it spawns carry e.g.
# --remote-control-session-...). A process with session flags is never the server.
$rcRx = '(^|\s|")remote-control("|\s|$)'
function Test-RcServer($x) { return ($x.cmd -match $rcRx -and $x.cmd -notmatch "$printRx|$bridgeRx") }
$items = New-Object System.Collections.Generic.List[object]
foreach ($p in $procs) {
    $isDesk = Test-Desktop $p
    $isCli = -not $isDesk -and (Test-Cli $p)
    if (-not ($isDesk -or $isCli)) { continue }
    $anc = Get-Ancestors $p
    $parent = Get-Parent $p
    $role = $null; $owner = $null
    if ($isDesk) {
        $role = if ($anc | Where-Object { Test-Desktop $_ }) { 'desktop-helper' } else { 'desktop' }
    } elseif (Test-RcServer $p) {
        # The same server can show up as two processes (a launcher such as ~\.local\bin\claude.exe or a
        # node shim, and the real binary it starts with the same arguments). Only the top one is the server.
        $cliAnc = $anc | Where-Object { Test-Cli $_ } | Select-Object -First 1
        if ($cliAnc -and (Test-RcServer $cliAnc)) { $role = 'nested'; $owner = $cliAnc.pid }
        else { $role = 'listener' }
    } else {
        $cliAnc = $anc | Where-Object { Test-Cli $_ } | Select-Object -First 1
        $deskAnc = $anc | Where-Object { Test-Desktop $_ } | Select-Object -First 1
        $topRc = @($anc | Where-Object { (Test-Cli $_) -and (Test-RcServer $_) }) | Select-Object -Last 1
        if ($topRc) {
            # Somewhere under a Remote Control server. The server may sit behind a launcher child whose command
            # line doesn't say remote-control, and a session may run its own claude children (hooks, -p calls).
            # A session = the first Claude Code process below the server that looks like one: it carries the
            # session flags (--print / stream-json / --sdk-url), or it has no Claude Code children of its own.
            $between = @(); foreach ($a in $anc) { if ($a.pid -eq $topRc.pid) { break }; if (Test-Cli $a) { $between += $a } }
            $sessionAbove = $between | Where-Object { $_.cmd -match "$printRx|$bridgeRx" -or -not $cliParents.ContainsKey($_.pid) } | Select-Object -First 1
            if (-not $sessionAbove -and ($p.cmd -match "$printRx|$bridgeRx" -or -not $cliParents.ContainsKey($p.pid))) {
                $role = 'rc-session'; $owner = $topRc.pid
            } else { $role = 'nested'; $owner = $(if ($sessionAbove) { $sessionAbove.pid } else { $cliAnc.pid }) }
        }
        elseif ($cliAnc) { $role = 'nested'; $owner = $cliAnc.pid }
        elseif ($deskAnc) { $role = 'desktop-code'; $owner = $deskAnc.pid }
        elseif ($p.cmd -match $printRx) { $role = 'headless' }
        elseif ($p.cmd -match $bridgeRx) { $role = 'rc-session' }   # its listener is gone: an orphan
        elseif (-not $parent) { $role = 'interactive' }              # terminal closed under it; treated as interactive
        else { $role = 'interactive' }
    }
    $taskCopy = $false
    if ($role -eq 'listener' -and $taskEngine) {
        $taskCopy = [bool]($taskEngine -contains $p.pid -or ($anc | Where-Object { $taskEngine -contains $_.pid }))
    }
    $items.Add([pscustomobject]@{
        pid = $p.pid; ppid = $p.ppid; role = $role; owner = $owner; parentAlive = [bool]$parent; taskCopy = $taskCopy
        created = $p.created; ageMin = [Math]::Round(($nowT - $p.created).TotalMinutes, 1); mb = $p.mb; cpu = $p.cpu
        name = $p.name; cmd = $p.cmd
        orphan = $false; action = ''; why = ''
    })
}
# A sub-process of a session (nested) adds its memory to that session's total but isn't a session itself.
# Desktop helpers are counted with their window.

# --- State from the last run (CPU samples, duplicate-listener sightings, last alert) -----
$statePath = Join-Path $root 'state.json'
$state = $null
try { if (Test-Path $statePath) { $state = Get-Content $statePath -Raw | ConvertFrom-Json } } catch { }
$prevCpu = @{}
$prevDup = @{}
if ($state -and $state.cpu) { $state.cpu.PSObject.Properties | ForEach-Object { $prevCpu[$_.Name] = $_.Value } }
if ($state -and $state.dup) { $state.dup.PSObject.Properties | ForEach-Object { $prevDup[$_.Name] = $_.Value } }
function Key($i) { "$($i.pid)@$($i.created.ToString('yyyyMMddHHmmss'))" }
# Idle = used under 2 CPU seconds since the previous run, which was at least 4 min ago.
function Test-Idle($i) {
    $prev = $prevCpu[(Key $i)]
    if (-not $prev) { return $false }
    $gap = ($nowT - [datetime]::Parse("$($prev.at)", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal)).TotalMinutes
    return ($gap -ge 4 -and ($i.cpu - [double]$prev.cpu) -lt 2)
}

# --- Decide what to clean up ----------------------------------------------------------------
$listeners = @($items | Where-Object { $_.role -eq 'listener' })
$sessionsOf = @{}
foreach ($l in $listeners) { $sessionsOf[$l.pid] = @($items | Where-Object { $_.role -eq 'rc-session' -and $_.owner -eq $l.pid }).Count }
$deskMains = @($items | Where-Object { $_.role -eq 'desktop' } | ForEach-Object { $_.pid })

foreach ($i in $items) {
    switch ($i.role) {
        'headless' {
            if (-not $i.parentAlive) { $i.orphan = $true; $i.action = 'stop'; $i.why = 'claude -p run whose parent (Worker/script) is gone' }
            elseif ($i.ageMin -gt $headlessMaxMin) { $i.action = 'stop'; $i.why = "claude -p run older than $headlessMaxMin min (Worker limit is 45)" }
        }
        'rc-session' {
            if (-not $i.owner) {
                $i.orphan = $true
                if (Test-Idle $i) { $i.action = 'stop'; $i.why = 'Remote Control session whose listener is gone, idle since the last check' }
                else { $i.why = 'listener gone; stopped next run if still idle' }
            }
        }
        'desktop-code' {
            if ($deskMains -notcontains $i.owner) {
                $i.orphan = $true
                if (Test-Idle $i) { $i.action = 'stop'; $i.why = 'desktop-app session whose app is gone, idle since the last check' }
            }
        }
        'interactive' {
            if (-not $i.parentAlive -and $i.cmd -match $bridgeRx) { $i.orphan = $true }
        }
    }
}
# Duplicate listeners. Keep every listener that has sessions; if none has, keep the task's copy (else the oldest).
# Stop a zero-session extra only when it was also an extra on the previous run, so a restart in progress is left alone.
$newDup = @{}
if ($listeners.Count -gt 1) {
    $keep = @($listeners | Where-Object { $sessionsOf[$_.pid] -gt 0 })
    if (-not $keep) {
        $keep = @($listeners | Sort-Object @{ Expression = { -not $_.taskCopy } }, created | Select-Object -First 1)
    }
    foreach ($l in $listeners) {
        # The task's own copy is never stopped: the watchdog would only start it again. And when this run can't
        # tell which copy is the task's (Task Scheduler didn't answer), nothing is stopped, only reported.
        if ($keep.pid -contains $l.pid -or $l.taskCopy -or -not $taskEngine) {
            if (-not ($keep.pid -contains $l.pid) -and -not $l.taskCopy) { $l.why = 'extra Remote Control server with no sessions (not stopped: the task copy could not be identified)' }
            continue
        }
        $k = Key $l
        $newDup[$k] = $nowT.ToString('o')
        $seenAt = if ($prevDup[$k]) { [datetime]::Parse("$($prevDup[$k])", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal) } else { $null }
        if ($seenAt) { $newDup[$k] = $seenAt.ToString('o') }   # keep the first sighting
        if ($seenAt -and ($nowT - $seenAt).TotalMinutes -ge 4) { $l.action = 'stop'; $l.why = 'duplicate Remote Control server with no sessions (second check in a row)' }
        else { $l.why = 'duplicate Remote Control server with no sessions; stopped next run if still idle' }
    }
}

# --- Clean up ----------------------------------------------------------------------------------
$stopped = New-Object System.Collections.Generic.List[object]
foreach ($i in @($items | Where-Object { $_.action -eq 'stop' })) {
    if ($DryRun) { $stopped.Add($i); continue }
    # taskkill /T takes the whole tree (MCP servers, shells the session started) with it.
    $out = & taskkill.exe /PID $i.pid /T /F 2>&1
    if ($LASTEXITCODE -eq 0 -or -not (Get-Process -Id $i.pid -ErrorAction SilentlyContinue)) {
        $stopped.Add($i)
        Log "Stopped $($i.role) pid $($i.pid) ($($i.why)), $($i.mb) MB: $((Redact $i.cmd).Substring(0, [Math]::Min(160, $i.cmd.Length)))"
    } else {
        $i.action = 'failed'
        Log "Could not stop $($i.role) pid $($i.pid): $out"
    }
}
$live = if ($DryRun) { @($items | Where-Object { $_.action -ne 'stop' }) } else { @($items | Where-Object { @($stopped.pid) -notcontains $_.pid }) }

# --- Counts and caps --------------------------------------------------------------------------
function Count-Role([string]$r) { @($live | Where-Object { $_.role -eq $r }).Count }
$memAll = [Math]::Round((@($live | Measure-Object mb -Sum).Sum), 0)
$totalMb = 0
if (-not $testMode) {
    try { $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop; $totalMb = [Math]::Round($os.TotalVisibleMemorySize / 1KB, 0); $freeMb = [Math]::Round($os.FreePhysicalMemory / 1KB, 0) } catch { }
} else { $totalMb = if ($env:GUARD_TEST_TOTAL_MB) { [double]$env:GUARD_TEST_TOTAL_MB } else { 16384 }; $freeMb = 4096 }
$counts = [ordered]@{
    listeners         = Count-Role 'listener'
    rc_sessions       = Count-Role 'rc-session'
    headless          = Count-Role 'headless'
    desktop_code      = Count-Role 'desktop-code'
    interactive       = Count-Role 'interactive'
    desktop_instances = Count-Role 'desktop'
    desktop_processes = (Count-Role 'desktop') + (Count-Role 'desktop-helper')
    nested            = Count-Role 'nested'
}
$counts['sessions_total'] = $counts.rc_sessions + $counts.headless + $counts.desktop_code + $counts.interactive
$counts['claude_mem_mb'] = $memAll
$counts['claude_mem_pct'] = if ($totalMb) { [Math]::Round(100 * $memAll / $totalMb, 0) } else { 0 }
$counts['free_mem_mb'] = $freeMb
$counts['total_mem_mb'] = $totalMb

$names = @{
    listeners = 'Remote Control servers'; rc_sessions = 'Remote Control sessions'; headless = 'claude -p runs'
    desktop_code = 'desktop-app Code sessions'; interactive = 'terminal sessions'; sessions_total = 'Claude Code sessions in all'
    desktop_instances = 'Claude desktop app windows'; claude_mem_pct = '% of RAM used by Claude'
}
$over = New-Object System.Collections.Generic.List[string]
foreach ($k in $caps.Keys) {
    if ($counts[$k] -gt $caps[$k]) { $over.Add("$($counts[$k]) $($names[$k]) (cap $($caps[$k]))") }
}
$orphansLeft = @($live | Where-Object { $_.orphan })
$summary = "$($counts.sessions_total) sessions ($($counts.rc_sessions) Remote Control, $($counts.headless) -p, $($counts.interactive) terminal, $($counts.desktop_code) desktop Code), " +
    "$($counts.listeners) RC server$(if ($counts.listeners -ne 1) { 's' }), desktop app $($counts.desktop_instances) window$(if ($counts.desktop_instances -ne 1) { 's' }) / $($counts.desktop_processes) processes, " +
    "Claude using $([Math]::Round($memAll / 1024, 1)) GB ($($counts.claude_mem_pct)% of RAM)"

# --- Process table (for the alert comment and the local snapshot) -------------------------------
function Short([string]$c) { $c = (Redact $c) -replace '\s+', ' '; if ($c.Length -gt 110) { $c.Substring(0, 110) + '...' } else { $c } }
$table = @('| PID | Role | Age | MB | Note | Command |', '|---|---|---|---|---|---|')
foreach ($i in @($items | Where-Object { $_.role -ne 'desktop-helper' } | Sort-Object role, created)) {
    $note = @($(if ($i.taskCopy) { 'task copy' }), $(if ($i.role -eq 'listener') { "$($sessionsOf[$i.pid]) sessions" }),
        $(if ($i.orphan) { 'orphan' }), $(if ($i.action -eq 'stop') { 'STOPPED' }), $(if ($i.action -eq 'failed') { 'stop failed' })) | Where-Object { $_ }
    $age = if ($i.ageMin -ge 120) { "$([Math]::Round($i.ageMin / 60, 1)) h" } else { "$([Math]::Round($i.ageMin)) m" }
    $table += "| $($i.pid) | $($i.role) | $age | $([Math]::Round($i.mb)) | $($note -join ', ') | ``$((Short $i.cmd).Replace('|', '/').Replace('`', "'"))`` |"
}
$helpers = @($items | Where-Object { $_.role -eq 'desktop-helper' })
if ($helpers) { $table += "| | desktop helpers | | $([Math]::Round((@($helpers | Measure-Object mb -Sum).Sum))) | $($helpers.Count) Electron helper processes | |" }

# --- Result object (status.json; also what the tests read) -----------------------------------
$alertText = if ($over.Count) { "Too many Claude processes on ${Machine}: " + ($over -join ', ') } else { '' }
$result = [ordered]@{
    machine = $Machine; at = $nowT.ToString('yyyy-MM-ddTHH:mm:ssZ'); counts = $counts; caps = $caps
    over = @($over); alert = $alertText; summary = $summary
    stopped = @($stopped | ForEach-Object { [ordered]@{ pid = $_.pid; role = $_.role; why = $_.why; mb = $_.mb } })
    orphans_left = @($orphansLeft | ForEach-Object { [ordered]@{ pid = $_.pid; role = $_.role; why = $_.why } })
    processes = @($items | Where-Object { $_.role -ne 'desktop-helper' } | ForEach-Object {
        [ordered]@{ pid = $_.pid; role = $_.role; owner = $_.owner; age_min = $_.ageMin; mb = $_.mb; task_copy = $_.taskCopy; orphan = $_.orphan; action = $_.action; why = $_.why; cmd = (Short $_.cmd) } })
}

# --- Save state ------------------------------------------------------------------------------------
$cpuOut = [ordered]@{}
foreach ($i in $live) { $cpuOut[(Key $i)] = @{ cpu = $i.cpu; at = $nowT.ToString('o') } }
$lastSig = if ($state) { "$($state.alert_sig)" } else { '' }
$lastPush = if ($state -and $state.alert_at) { [datetime]::Parse("$($state.alert_at)", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal) } else { [datetime]::MinValue }
$sig = ($over | ForEach-Object { ($_ -replace '^\d+ ', '') }) -join '; '    # which caps are exceeded, not by how much
$newState = [ordered]@{ cpu = $cpuOut; dup = $newDup; alert_sig = $lastSig; alert_at = $(if ($lastPush -gt [datetime]::MinValue) { $lastPush.ToString('o') } else { $null })
    fleet_issue = $(if ($state) { $state.fleet_issue }); fleet_comment = $(if ($state) { $state.fleet_comment }) }

# Decide on the alert now so tests can see it.
$notify = 'none'
if ($sig -and ($sig -ne $lastSig -or ($nowT - $lastPush).TotalHours -ge $repeatHours)) { $notify = 'alert' }
elseif (-not $sig -and $lastSig) { $notify = 'cleared' }
$result['notify'] = $notify

if ($testMode) {
    # Tests: behave as if the push went through.
    if ($notify -ne 'none') { $newState.alert_sig = $sig; $newState.alert_at = $(if ($sig) { $nowT.ToString('o') } else { $null }) }
    $newState | ConvertTo-Json -Depth 6 | Set-Content -Path $statePath -Encoding UTF8
    $result | ConvertTo-Json -Depth 6
    return
}

$result | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $root 'status.json') -Encoding UTF8
$hist = Join-Path $root 'history.csv'
if ((Test-Path $hist) -and (Get-Item $hist).Length -gt 2MB) { Move-Item $hist "$hist.old" -Force }
if (-not (Test-Path $hist)) { 'time,listeners,rc_sessions,headless,interactive,desktop_code,desktop_instances,desktop_processes,claude_mem_mb,free_mem_mb,stopped' | Set-Content $hist -Encoding ASCII }
"$($result.at),$($counts.listeners),$($counts.rc_sessions),$($counts.headless),$($counts.interactive),$($counts.desktop_code),$($counts.desktop_instances),$($counts.desktop_processes),$memAll,$freeMb,$($stopped.Count)" | Add-Content $hist -Encoding ASCII
if ($stopped.Count -or $over.Count) {
    $snapDir = Join-Path $root 'snapshots'
    New-Item -ItemType Directory -Force -Path $snapDir | Out-Null
    @("$($result.at) $Machine", $summary, $alertText, '') + $table | Set-Content (Join-Path $snapDir "$(Get-Date -Format 'yyyyMMdd-HHmmss').md") -Encoding UTF8
    Get-ChildItem $snapDir -Filter *.md | Sort-Object LastWriteTime -Descending | Select-Object -Skip 200 | Remove-Item -Force -ErrorAction SilentlyContinue
}

# --- GitHub: Fleet panel line, Health comment ---------------------------------------------------------
$ghTok = @('User', 'Machine', 'Process') | ForEach-Object { [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', $_) } | Where-Object { $_ } | Select-Object -First 1
$ghRepo = @([Environment]::GetEnvironmentVariable('JARVIS_TASKS_REPO', 'User'), 'flanneryjake/jarvis-tasks') | Where-Object { $_ } | Select-Object -First 1
$ghHdr = @{ Authorization = "Bearer $ghTok"; Accept = 'application/vnd.github+json'; 'User-Agent' = 'jarvis-claude-guard' }
function Invoke-GH([string]$method, [string]$path, $body) {
    $a = @{ Method = $method; Uri = "https://api.github.com/repos/$ghRepo/$path"; Headers = $ghHdr; TimeoutSec = 30 }
    if ($null -ne $body) { $a.Body = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 8)); $a.ContentType = 'application/json; charset=utf-8' }
    Invoke-RestMethod @a
}

if ($ghTok -and -not $DryRun) {
    # 1) This PC's line on the pinned "Fleet control" issue. fleet.py reads it into the phone app's Fleet panel.
    $beat = [ordered]@{ at = $result.at; counts = $counts; caps = $caps; over = @($over); stopped = $stopped.Count; summary = $summary }
    $fbody = "<!-- jarvis:claudewatch $Machine $(($beat | ConvertTo-Json -Depth 4 -Compress)) -->`n" +
        "**$Machine** Claude processes at $($result.at): $summary" + $(if ($over.Count) { "`n> $alertText" } else { '' })
    try {
        $fi = $newState.fleet_issue
        if (-not $fi) {
            $fi = @(Invoke-GH Get 'issues?state=open&labels=fleet&per_page=20' | ForEach-Object { $_ } | Where-Object { "$($_.title)" -like 'Fleet control*' } | Select-Object -First 1).number
            $newState.fleet_issue = $fi
        }
        if ($fi) {
            $done = $false
            if ($newState.fleet_comment) {
                try { Invoke-GH Patch "issues/comments/$($newState.fleet_comment)" @{ body = $fbody } | Out-Null; $done = $true }
                catch { Log "Fleet line update failed, looking it up again: $(Redact $_.Exception.Message)"; $newState.fleet_comment = $null }
            }
            if (-not $done) {
                $mine = $null
                for ($pg = 1; $pg -le 5 -and -not $mine; $pg++) {
                    $cs = @(Invoke-GH Get "issues/$fi/comments?per_page=100&page=$pg" | ForEach-Object { $_ })
                    $mine = $cs | Where-Object { "$($_.body)" -like "<!-- jarvis:claudewatch $Machine *" } | Select-Object -First 1
                    if ($cs.Count -lt 100) { break }
                }
                if ($mine) { Invoke-GH Patch "issues/comments/$($mine.id)" @{ body = $fbody } | Out-Null; $newState.fleet_comment = $mine.id }
                else { $c = Invoke-GH Post "issues/$fi/comments" @{ body = $fbody }; $newState.fleet_comment = $c.id }
            }
        }
    } catch { Log "Fleet line failed: $(Redact $_.Exception.Message)" }
}

# 2) Phone push and Health-issue comment when a cap is exceeded (new problem, or still going after 3 h), and a
#    comment when it clears. Pushes go through the hub on homebase; the other PCs reach it over the tailnet.
$hubUrls = if ($Machine -eq 'homebase') { @('http://127.0.0.1:8770/api/notify', 'http://127.0.0.1:8765/api/notify') }
           else { @($(if ($cfg -and $cfg.hub_notify_url) { $cfg.hub_notify_url } else { 'https://laptop-4150egrs.tail3bbcb8.ts.net/api/notify' })) }
function Send-Push([string]$title, [string]$text) {
    $json = @{ title = $title; body = $text; message = $text; tag = "jarvis-claude-$Machine"; url = "https://github.com/$ghRepo/issues?q=is%3Aopen+label%3Ahealth" } | ConvertTo-Json
    foreach ($u in $hubUrls) {
        try {
            Invoke-RestMethod -Method Post -Uri $u -Headers @{ 'X-Jarvis-Notify' = '1' } -Body ([Text.Encoding]::UTF8.GetBytes($json)) -ContentType 'application/json; charset=utf-8' -TimeoutSec 10 | Out-Null
            Log "Push sent via $u : $title - $text"
            return $true
        } catch { Log "Push via $u failed: $($_.Exception.Message)" }
    }
    return $false
}
if ($TestPush) {
    if (Send-Push 'Jarvis test' "Test push from the $Machine Claude guard") { 'Push sent.' } else { "Push failed; see $log" }
    return
}
function Get-HealthNumber {
    $f = 'C:\Jarvis\watchdog\health-issue.txt'
    $n = "$(Get-Content $f -TotalCount 1 -ErrorAction SilentlyContinue)".Trim()
    if ($n -match '^\d+$') { return $n }
    try {
        $h = @(Invoke-GH Get 'issues?state=open&labels=health&per_page=100' | ForEach-Object { $_ }) |
            Where-Object { "$($_.title)" -match "^Health: $Machine(\s|$)" } | Sort-Object number | Select-Object -First 1
        if ($h) { return $h.number }
    } catch { }
    return $null
}
if (-not $DryRun -and $notify -ne 'none') {
    $pushOk = $true
    if ($notify -eq 'alert') {
        $short = ($over -join ', ') + $(if ($stopped.Count) { "; cleaned up $($stopped.Count) orphan$(if ($stopped.Count -ne 1) { 's' })" } else { '' })
        $pushOk = Send-Push "Jarvis: too many Claude processes on $Machine" $short
    }
    if ($ghTok) {
        $hn = Get-HealthNumber
        if ($hn) {
            $cb = if ($notify -eq 'alert') {
                @("**Claude guard: too many Claude processes on $Machine** ($($result.at))", '', "> $alertText", '', $summary, '') +
                $(if ($stopped.Count) { @("Cleaned up this run: $(@($stopped | ForEach-Object { "pid $($_.pid) ($($_.role): $($_.why))" }) -join '; ')", '') } else { @() }) +
                $table + @('', 'Nothing with live work is stopped automatically. Close extra sessions, or ask Claude in the project to.')
            } else { @("**Claude guard: $Machine is back under its caps** ($($result.at)). $summary") }
            $cb += @('', '---', '_Generated by [Claude Code](https://claude.ai/code)_')
            try { Invoke-GH Post "issues/$hn/comments" @{ body = ($cb -join "`n") } | Out-Null } catch { Log "Health comment failed: $(Redact $_.Exception.Message)" }
        }
    }
    if ($pushOk) {
        $newState.alert_sig = $sig
        $newState.alert_at = $(if ($sig) { $nowT.ToString('o') } else { $null })
    }
}
# A report-only run (the installer's first check) leaves no samples behind, so it can't count as a first sighting.
if (-not $DryRun) { $newState | ConvertTo-Json -Depth 6 | Set-Content -Path $statePath -Encoding UTF8 }
if ($stopped.Count -or $over.Count) { Log "$summary$(if ($alertText) { " | $alertText" })" }
if ($DryRun) { $result | ConvertTo-Json -Depth 6 }
