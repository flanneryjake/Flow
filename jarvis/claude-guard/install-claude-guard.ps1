# Jarvis Claude guard installer: run once per PC (homebase = the 5060, rig, backup = junk laptop). Needs admin,
# because the guard runs elevated: the 5060's Jarvis services run elevated, and a normal process can't read
# their command lines or stop them.
#
# One click: "Install Claude guard.cmd" (next to this file in Flow) asks for admin and runs this. By hand, in an
# ADMIN PowerShell window:
#
#   $t=[Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN','User'); irm -Headers @{Authorization="Bearer $t"; Accept='application/vnd.github.raw'} 'https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/claude-guard/install-claude-guard.ps1?ref=claude/eager-knuth-lakcxt' | iex
#
# What it does:
#   - saves claude-guard.ps1 to C:\Jarvis\claude-guard (config.json there is kept if you already have one)
#   - registers "Jarvis Claude Guard": every 5 min and at startup, as this user, elevated, with no window
#     (S4U, so it also runs while nobody is logged in)
#   - runs it once in report-only mode and prints what it sees
#   - homebase only (it serves the phone app): updates fleet.py and fleet-panel.js so System > Fleet shows each
#     PC's Claude counts, but only where they are unmodified Flow copies (old copies kept as *.bak-guard-<time>);
#     then restarts the hub if C:\Jarvis\tools\restart-hub-agent.ps1 is there
# Without admin it installs a limited version (runs while the user is logged in, skips elevated processes).
# Nothing is stopped by installing. Uninstall: Unregister-ScheduledTask 'Jarvis Claude Guard'.

$ErrorActionPreference = 'Stop'
$flowRef = 'claude/eager-knuth-lakcxt'
function Get-FlowFile([string]$path, [string]$out, [string]$ref = $flowRef) {
    $tok = if ($env:GITHUB_TASKS_TOKEN) { $env:GITHUB_TASKS_TOKEN } else { [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User') }
    $h = @{ Accept = 'application/vnd.github.raw'; 'User-Agent' = 'jarvis-installer' }
    if ($tok) { $h.Authorization = "Bearer $tok" }
    try { Invoke-WebRequest -UseBasicParsing -Headers $h "https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/$path`?ref=$ref" -OutFile $out }
    catch { throw "Could not download jarvis/$path from Flow ($_). Check that GITHUB_TASKS_TOKEN can read flanneryjake/Flow." }
}
function Say([string]$m, [string]$c = 'Cyan') { Write-Host $m -ForegroundColor $c }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    # Still useful without admin: it then runs as a normal task while this user is logged in, and can't see or
    # stop elevated processes (fine on a PC whose Jarvis services aren't elevated). Re-run as admin to upgrade.
    Say 'Not running as admin: installing the limited version (runs while you are logged in, skips elevated processes).' 'Yellow'
}
if (-not [Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN', 'User')) { throw 'GITHUB_TASKS_TOKEN is not set for this user.' }

$machine = switch ($env:COMPUTERNAME.ToUpper()) {
    'LAPTOP-4150EGRS' { 'homebase' }
    'DESKTOP-VLLDDM4' { 'rig' }
    'DESKTOP-5VE3C77' { 'backup' }
    default { "$(Get-Content 'C:\Jarvis\watchdog\machine.txt' -TotalCount 1 -ErrorAction SilentlyContinue)".Trim() }
}
if (-not $machine) { throw "Unknown PC $env:COMPUTERNAME and no C:\Jarvis\watchdog\machine.txt." }
Say "== Claude guard for $machine =="

$dir = 'C:\Jarvis\claude-guard'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Get-FlowFile 'claude-guard/claude-guard.ps1' (Join-Path $dir 'claude-guard.ps1')
if (-not (Test-Path (Join-Path $dir 'config.json'))) { Get-FlowFile 'claude-guard/config.json' (Join-Path $dir 'config.json') }
Say "Saved the guard to $dir"

$user = "$env:USERDOMAIN\$env:USERNAME"
$psArgs = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\claude-guard.ps1`""
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
if ($admin) {
    # S4U runs in the background session: no window ever, and it runs while nobody is logged in.
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $dir -Argument $psArgs
    $boot = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest
} else {
    # In the user's session powershell flashes a console even with -WindowStyle Hidden, so start it through a .vbs.
    $vbs = Join-Path $dir 'run-guard.vbs'
    @('Set sh = CreateObject("WScript.Shell")', "WScript.Quit sh.Run(""powershell.exe $($psArgs.Replace('"', '""'))"", 0, True)") |
        Set-Content -Path $vbs -Encoding ASCII
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -WorkingDirectory $dir -Argument "`"$vbs`""
    $boot = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
}
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Jarvis Claude Guard' -Action $action -Trigger @($every, $boot) -Principal $principal `
    -Settings $settings -Description 'Counts Claude processes, cleans up orphans, alerts Jake over a cap (Flow jarvis/claude-guard).' -Force | Out-Null
Say "Registered `"Jarvis Claude Guard`" (every 5 min, $(if ($admin) { 'elevated' } else { 'limited' }), no window)."

if ($machine -eq 'homebase') {
    # Fleet files are only replaced where they are still Flow's copy from before the guard (aa92b54) or already
    # carry it. A PC with its own changes (e.g. the 5060's rig-first gate) keeps its file and is reported instead.
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $baseRef = 'aa92b5454d4ed2e5e7fac04d5b4889a15456e0b5'
    $fleetDir = 'C:\Jarvis\fleet'
    $tmp = Join-Path $env:TEMP "claude-guard-$stamp"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    function Norm([string]$f) { ([IO.File]::ReadAllText($f)).Replace("`r`n", "`n").TrimEnd() }
    $changed = $false
    if (Test-Path $fleetDir) {
        # fleet_api.py also reads the counts, so the panel shows them even where fleet.py has local changes.
        foreach ($f in 'fleet.py', 'fleet_api.py', 'fleet-panel.js') {
            Get-FlowFile "fleet/$f" (Join-Path $tmp "$f.base") $baseRef
            Get-FlowFile "fleet/$f" (Join-Path $tmp $f)
            $targets = @(Join-Path $fleetDir $f)
            if ($f -eq 'fleet-panel.js') {
                # The hub serves its own copy of the panel script from its web folder.
                $targets += @(Get-ChildItem 'C:\Jarvis' -Recurse -Filter $f -ErrorAction SilentlyContinue |
                    Where-Object { $_.DirectoryName -ne $fleetDir -and $_.FullName -notmatch '\\(node_modules|\.git|outputs-repo|audit)\\|\.bak' } |
                    ForEach-Object { $_.FullName })
            }
            foreach ($dest in $targets) {
                if (-not (Test-Path $dest)) { continue }
                $cur = Norm $dest
                if ($cur -match 'claudewatch|claudeLine') { Say "  $dest already shows Claude counts" 'Green'; continue }
                if ($cur -ne (Norm (Join-Path $tmp "$f.base"))) {
                    Say "  $dest has local changes; left as is (Claude counts won't show in the app from it until it is merged with Flow)" 'Yellow'
                    continue
                }
                Copy-Item $dest "$dest.bak-guard-$stamp"
                Copy-Item (Join-Path $tmp $f) $dest -Force
                Say "  Updated $dest" 'Green'
                $changed = $true
            }
        }
        $restart = 'C:\Jarvis\tools\restart-hub-agent.ps1'
        if ($changed -and (Test-Path $restart)) {
            Say 'Restarting the hub so the Fleet panel shows Claude counts (the agent restarts once it is idle)...'
            & powershell -NoProfile -ExecutionPolicy Bypass -File $restart
        } elseif ($changed) { Say 'Restart the hub to see Claude counts in System > Fleet.' 'Yellow' }
    } else { Say 'No C:\Jarvis\fleet here; the Fleet panel was not updated.' 'Yellow' }
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Say ''
Say 'First check (report only, nothing stopped):'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'claude-guard.ps1') -DryRun |
    ConvertFrom-Json | ForEach-Object { Say "  $($_.summary)" 'Green'; if ($_.alert) { Say "  $($_.alert)" 'Yellow' }
        foreach ($s in @($_.stopped)) { Say "  would clean up pid $($s.pid) ($($s.role)): $($s.why)" 'Yellow' } }
Start-ScheduledTask -TaskName 'Jarvis Claude Guard'
Say ''
Say '== Done. The guard runs every 5 min; its line is on the pinned Fleet control issue and in the app (System > Fleet). ==' 'Green'
