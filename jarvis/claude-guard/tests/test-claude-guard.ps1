# Tests for claude-guard.ps1, using recorded process lists instead of Windows (runs on Windows PowerShell 5.1 or pwsh).
#   powershell -NoProfile -ExecutionPolicy Bypass -File jarvis\claude-guard\tests\test-claude-guard.ps1
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$guard = Join-Path (Split-Path $here -Parent) 'claude-guard.ps1'
$state = Join-Path ([IO.Path]::GetTempPath()) "claude-guard-test-$PID"
$fails = 0
function Check([string]$what, $ok) {
    if ($ok) { Write-Host "ok    $what" } else { Write-Host "FAIL  $what" -ForegroundColor Red; $script:fails++ }
}
function Run([string]$fixture, [string]$now) {
    $out = & $guard -ProcessJson (Join-Path $here $fixture) -StateDir $state -Now $now -Machine homebase | Out-String
    return $out | ConvertFrom-Json
}
function Proc($r, [int]$id) { $r.processes | Where-Object { $_.pid -eq $id } }

Remove-Item $state -Recurse -Force -ErrorAction SilentlyContinue
$env:GUARD_TEST_TASK_ENGINE = '200'

# Run 1: the 10/02 pileup.
$r = Run 'incident-run1.json' '2026-10-02T23:00:00Z'
Check 'desktop app counted as one window' ($r.counts.desktop_instances -eq 1)
Check 'desktop helpers counted as processes' ($r.counts.desktop_processes -eq 12)
Check 'task listener is the task copy' ((Proc $r 202).task_copy -eq $true -and (Proc $r 202).role -eq 'listener')
Check 'two listeners seen' ($r.counts.listeners -eq 2)
Check 'sessions under the listener are rc-session' ((Proc $r 210).role -eq 'rc-session' -and (Proc $r 211).role -eq 'rc-session')
Check 'claude under a session is nested' ((Proc $r 213).role -eq 'nested')
Check 'desktop Code session' ((Proc $r 700).role -eq 'desktop-code')
Check 'interactive session' ((Proc $r 600).role -eq 'interactive')
Check 'headless with a live parent kept' ((Proc $r 500).action -eq '')
Check 'headless with a dead parent stopped' ((Proc $r 510).action -eq 'stop' -and (Proc $r 510).orphan)
Check 'headless over 75 min stopped' ((Proc $r 520).action -eq 'stop')
Check 'PID reuse: a parent younger than its child does not count' ((Proc $r 530).action -eq 'stop')
Check 'orphan rc-session waits for a second idle sample' ((Proc $r 400).orphan -and (Proc $r 400).action -eq '')
Check 'duplicate listener waits for a second sighting' ((Proc $r 300).action -eq '' -and (Proc $r 300).why -like 'duplicate*')
Check 'listener with sessions never stopped' ((Proc $r 202).action -eq '')
Check 'interactive and desktop never stopped' ((Proc $r 600).action -eq '' -and (Proc $r 100).action -eq '' -and (Proc $r 700).action -eq '')
Check 'over the listener cap' (@($r.over | Where-Object { $_ -like '*Remote Control servers*' }).Count -eq 1)
Check 'alert raised' ($r.notify -eq 'alert' -and $r.alert -like 'Too many Claude processes on homebase*')
Check 'three stopped' (@($r.stopped).Count -eq 3)

# Run 2, five minutes later: the orphan session used no CPU and the extra listener is still there.
$r2 = Run 'incident-run2.json' '2026-10-02T23:05:00Z'
Check 'idle orphan rc-session stopped' ((Proc $r2 400).action -eq 'stop')
Check 'busy rc-session kept' ((Proc $r2 210).action -eq '' -and (Proc $r2 211).action -eq '')
Check 'duplicate listener stopped on second sighting' ((Proc $r2 300).action -eq 'stop')
Check 'task listener kept' ((Proc $r2 202).action -eq '')
Check 'back under the caps once the extras are gone' ($r2.notify -eq 'cleared' -and @($r2.over).Count -eq 0)

# Clean PC: nothing to do, nothing more to say.
$r3 = Run 'clean.json' '2026-10-02T23:10:00Z'
Check 'clean PC: no new notice' ($r3.notify -eq 'none')
Check 'clean PC: one listener, no sessions' ($r3.counts.listeners -eq 1 -and $r3.counts.sessions_total -eq 0)
Check 'clean PC: nothing over a cap' (@($r3.over).Count -eq 0)
Check 'clean PC: nothing stopped' (@($r3.stopped).Count -eq 0)

# The same problem twice in a row is pushed once, and again after 3 h.
Remove-Item $state -Recurse -Force -ErrorAction SilentlyContinue
$a = Run 'six-threads.json' '2026-10-02T23:00:00Z'
$b = Run 'six-threads.json' '2026-10-02T23:05:00Z'
$c = Run 'six-threads.json' '2026-10-03T02:06:00Z'
Check 'same problem pushed once' ($a.notify -eq 'alert' -and $b.notify -eq 'none')
Check 'still going after 3 h: pushed again' ($c.notify -eq 'alert')
Check 'six busy thread sessions: alert, nothing stopped' (@($a.stopped).Count -eq 0 -and $a.alert -like '*6 Remote Control sessions (cap 4)*')

# A launcher that starts the real binary with the same arguments is one server, not two.
Remove-Item $state -Recurse -Force -ErrorAction SilentlyContinue
$l = Run 'launcher-shim.json' '2026-10-02T23:00:00Z'
Check 'launcher child is nested, not a second server' ((Proc $l 901).role -eq 'nested' -and (Proc $l 900).role -eq 'listener')
Check 'session belongs to the top server' ((Proc $l 902).role -eq 'rc-session' -and (Proc $l 902).owner -eq 900)
Check 'two real servers counted' ($l.counts.listeners -eq 2)
$l = Run 'launcher-shim.json' '2026-10-02T23:06:00Z'
Check 'task copy is never stopped, even idle next to a busy hand copy' ((Proc $l 202).action -eq '' -and (Proc $l 900).action -eq '')

# Without Task Scheduler's answer, no Remote Control server is stopped.
Remove-Item $state -Recurse -Force -ErrorAction SilentlyContinue
$env:GUARD_TEST_TASK_ENGINE = ''
$u1 = Run 'incident-run1.json' '2026-10-02T23:00:00Z'
$u2 = Run 'incident-run2.json' '2026-10-02T23:05:00Z'
Check 'unknown task copy: extra server reported, not stopped' ((Proc $u2 300).action -eq '' -and (Proc $u2 300).why -like '*could not be identified*')
$env:GUARD_TEST_TASK_ENGINE = '200'

# Rig layout: server -> real binary (no remote-control in its command line) -> 3 --print sessions, one running a -p hook.
Remove-Item $state -Recurse -Force -ErrorAction SilentlyContinue
$g = Run 'rig-launcher.json' '2026-10-02T23:00:00Z'
Check 'rig: launcher child is nested' ((Proc $g 950).role -eq 'nested')
Check 'rig: --print children of the server are its sessions' ($g.counts.rc_sessions -eq 3 -and (Proc $g 951).owner -eq 202)
Check 'rig: a -p call inside a session is part of it' ((Proc $g 960).role -eq 'nested' -and $g.counts.headless -eq 0)
Check 'rig: one server' ($g.counts.listeners -eq 1)

# Rig, 10/02: sessions whose flags contain the word remote-control (--remote-control-session-id) are sessions.
Remove-Item $state -Recurse -Force -ErrorAction SilentlyContinue
$h = Run 'rig-sessions.json' '2026-10-02T23:00:00Z'
Check 'rig: --remote-control-session-* children are sessions, not servers' ($h.counts.rc_sessions -eq 3 -and $h.counts.listeners -eq 1 -and (Proc $h 970).role -eq 'rc-session')

# A lone listener with no sessions is never a duplicate.
Remove-Item $state -Recurse -Force -ErrorAction SilentlyContinue
$r4 = Run 'clean.json' '2026-10-02T23:00:00Z'
$r4 = Run 'clean.json' '2026-10-02T23:05:00Z'
Check 'single idle listener kept' ((Proc $r4 202).action -eq '')

Remove-Item $state -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item Env:\GUARD_TEST_TASK_ENGINE
if ($fails) { Write-Host "$fails failed" -ForegroundColor Red; exit 1 } else { Write-Host 'all passed' -ForegroundColor Green }
