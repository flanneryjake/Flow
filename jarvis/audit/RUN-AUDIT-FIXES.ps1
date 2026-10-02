# Overnight audit 10/02: the 5060 fixes that need admin, in one run.
# Run once on the 5060 (LAPTOP-4150EGRS) from an admin PowerShell. Backs up every file it touches to
# C:\Jarvis\audit\backup-<time>\ and logs each step as OK / FAIL / SKIP to C:\Jarvis\audit\RUN-AUDIT-FIXES.log.
# A failed step does not stop the rest. No reboots, nothing deleted.
param([string]$Ref = '2137249', [switch]$DryRun)

$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'LAPTOP-4150EGRS') { throw "This is for the 5060 (LAPTOP-4150EGRS), not $env:COMPUTERNAME." }
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Open PowerShell as administrator and run this again.' }

$audit  = 'C:\Jarvis\audit'
$stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$bak    = Join-Path $audit "backup-$stamp"
$logf   = Join-Path $audit 'RUN-AUDIT-FIXES.log'
New-Item -ItemType Directory -Force -Path $bak | Out-Null
$results = [ordered]@{}
function Log([string]$m) { $line = "$(Get-Date -Format s) $m"; Add-Content -Path $logf -Value $line; Write-Host $line }
function Step([string]$name, [scriptblock]$body) {
    try {
        if ($DryRun) { $results[$name] = 'DRY RUN'; Log "DRY  $name"; return }
        $note = & $body
        $results[$name] = "OK $note".Trim(); Log "OK   $name $note"
    } catch { $results[$name] = "FAIL $($_.Exception.Message)"; Log "FAIL $name : $($_.Exception.Message)" }
}
function Backup([string]$path) {
    if (Test-Path $path) {
        $dest = Join-Path $bak (($path -replace '^[A-Za-z]:\\', '') -replace '\\', '_')
        Copy-Item $path $dest -Force
    }
}
$tok = [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User')
if (-not $tok) { $tok = $env:GITHUB_TASKS_TOKEN }
function Get-Flow([string]$path, [string]$out) {
    if (-not $tok) { throw 'GITHUB_TASKS_TOKEN is not set for this user.' }
    $h = @{ Authorization = "Bearer $tok"; Accept = 'application/vnd.github.raw'; 'User-Agent' = 'jarvis-audit' }
    $tmp = "$out.new"
    Invoke-WebRequest -UseBasicParsing -Headers $h -OutFile $tmp "https://api.github.com/repos/flanneryjake/Flow/contents/$path`?ref=$Ref"
    if ((Get-Item $tmp).Length -lt 200) { Remove-Item $tmp -Force; throw "download of $path looks empty" }
    Backup $out
    Move-Item $tmp $out -Force
}
function Find-Task([string]$pattern) {
    Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        $_.TaskName -like $pattern -or (($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join ' ') -like $pattern }
}
function Restart-Task([string]$name) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    Start-ScheduledTask -TaskName $name
}
Log "=== RUN-AUDIT-FIXES start (Flow ref $Ref, backups in $bak) ==="

# 1. ghq with PR #31 (dedupe, snooze, homebase/backup names), staged by the app-notes thread
Step 'ghq.py (PR #31)' {
    $staged = 'C:\Users\Jake\AppData\Local\Temp\claude-merge\ghq.staged.py'
    if (-not (Test-Path $staged)) { return 'SKIP: no staged ghq.py' }
    & python -m py_compile $staged; if ($LASTEXITCODE) { throw 'staged ghq.py does not compile' }
    Backup 'C:\Jarvis\ghq\ghq.py'
    Copy-Item $staged 'C:\Jarvis\ghq\ghq.py' -Force
}

# 2. Fleet roles: homebase = this 5060, backup = junk laptop
Step 'fleet roles.json' { Get-Flow 'jarvis/fleet/roles.json' 'C:\Jarvis\fleet\roles.json' }

# 3. Machine name: this PC is homebase (matches the Worker's claimed:homebase labels)
Step 'machine.txt = homebase' {
    Backup 'C:\Jarvis\watchdog\machine.txt'
    Set-Content -Path 'C:\Jarvis\watchdog\machine.txt' -Value 'homebase' -Encoding ASCII
}

# 4. Watchdog: one Health issue per machine, this PC on Health #1, pushes for the rig and backup too
Step 'watchdog.ps1 + Health #1' {
    Get-Flow 'jarvis/watchdog.ps1' 'C:\Jarvis\watchdog\watchdog.ps1'
    Backup 'C:\Jarvis\watchdog\health-issue.txt'
    Set-Content -Path 'C:\Jarvis\watchdog\health-issue.txt' -Value '1' -Encoding ASCII
}

# 5. Outputs sync: it never pulled, so pushes were rejected after 20:51. One catch-up pull + push.
Step 'outputs-repo catch-up push' {
    $repo = 'C:\Jarvis\outputs-repo'
    $ErrorActionPreference = 'Continue'   # git writes progress to stderr; judge by exit codes instead
    git -C $repo pull --rebase --autostash origin main 2>&1 | ForEach-Object { Log "  git: $_" }
    if ($LASTEXITCODE) { git -C $repo rebase --abort 2>$null; throw 'pull --rebase failed (rebase aborted, nothing lost)' }
    git -C $repo push origin HEAD:main 2>&1 | ForEach-Object { Log "  git: $_" }
    if ($LASTEXITCODE) { throw 'push failed' }
}

# 6. Crash recovery: hub, agent and Worker come back on their own; the junk-laptop-only HA export stays off here
Step 'restart-on-failure' {
    $done = @()
    foreach ($n in 'Jarvis HB Hub', 'Jarvis HB Agent', 'Jarvis Worker') {
        $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
        if (-not $t) { continue }
        $s = $t.Settings; $s.RestartCount = 999; $s.RestartInterval = 'PT1M'
        Set-ScheduledTask -TaskName $n -Settings $s | Out-Null; $done += $n
    }
    if (Get-ScheduledTask -TaskName 'Jarvis HB Routine Export' -ErrorAction SilentlyContinue) {
        Disable-ScheduledTask -TaskName 'Jarvis HB Routine Export' | Out-Null; $done += 'Routine Export disabled'
    }
    $done -join ', '
}

# 6b. Small leftovers: the laptop Worker still polls Notion (retired), and :8766 is a redundant redirect
Step 'JARVIS_QUEUE = github' { [Environment]::SetEnvironmentVariable('JARVIS_QUEUE', 'github', 'User') }
Step 'tailscale serve :8766 off' {
    $ErrorActionPreference = 'Continue'
    tailscale serve --https=8766 off 2>&1 | ForEach-Object { Log "  tailscale: $_" }
    if ($LASTEXITCODE) { throw 'tailscale serve off failed' }
}

# 7. Restarts, so tonight's edits go live: hub + agent, Worker (missing-input check), waker (hostname fixes)
Step 'restart hub + agent' {
    $r = 'C:\Jarvis\tools\restart-hub-agent.ps1'
    if (Test-Path $r) { & $r; 'via restart-hub-agent.ps1' }
    else { foreach ($n in 'Jarvis HB Hub', 'Jarvis HB Agent') { Restart-Task $n }; 'via task restart' }
}
Step 'restart Worker' { Restart-Task 'Jarvis Worker' }
Step 'restart waker' {
    $w = @(Find-Task '*waker*')
    if (-not $w) { throw 'no waker task found' }
    foreach ($t in $w) { Restart-Task $t.TaskName }
    ($w.TaskName -join ', ')
}

# 8. Exactly one Worker and one waker? (counts for the agent are reported, not judged)
Start-Sleep -Seconds 20
Step 'one of each process' {
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine }
    $counts = [ordered]@{}
    foreach ($k in 'worker.ps1', 'waker.ps1', 'agent.py') {
        $counts[$k] = @($procs | Where-Object { $_.CommandLine -like "*$k*" -and $_.Name -notlike 'wscript*' }).Count
    }
    $txt = ($counts.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ' '
    if ($counts['worker.ps1'] -gt 1 -or $counts['waker.ps1'] -gt 1) { throw "duplicates: $txt" }
    $txt
}

Log '=== done ==='
''
'Summary:'
$results.GetEnumerator() | ForEach-Object { '  {0,-28} {1}' -f $_.Key, $_.Value }
"Log: $logf   Backups: $bak"
