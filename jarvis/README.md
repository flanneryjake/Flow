# Jarvis Always-On

Makes homebase and the rig reachable from Jake's phone and from cloud Claude sessions, and makes failures visible.

Why this exists: cloud Claude sessions can't join Tailscale, so they can never connect *in* to a home PC.
Everything here has the home PC connect *out* instead:

- **Remote Control** (`claude remote-control`) dials out to claude.ai, so the PC shows up as a session in the
  Claude app and can take work from the phone or the cloud.
- **Watchdog** (every 5 min) restarts Remote Control when it dies (it exits after ~10 min offline) and writes the
  machine's row in the Notion **🩺 Machine Health** table (under Jarvis Command Center), so a stall shows up in
  Notion instead of going silent.

## Machine Health table

| Column | Meaning |
| --- | --- |
| Health | 🟢 OK, 🟠 plus the alert, or 🔴 Offline once the last check-in is over 15 min old (a Notion formula, so it works even when the PC is off) |
| Remote Control | Up, Restarted (was down, watchdog brought it back), **Down** (still down after every restart step; the alert says "restart failed" and is pushed to the phone), Task missing |
| Worker | Working, Idle, **Idle with cards waiting**, Paused (usage limit), Unknown |
| Waiting cards | Approved + Auto-executable cards for this machine or Any with nothing in `Claimed by`. On a PC whose Worker reads GitHub (`JARVIS_QUEUE=github`): open `status:approved` issues with no `claimed:*` label for this machine or any, plus Notion cards still Approved that have no GitHub copy |
| Last claim / Last claimed card | Newest `Claimed by` stamp this machine wrote on the Tasks board |
| Alert | Why the row isn't green |
| Snapshot | Full text: Remote Control, ports, Jarvis scheduled tasks, tail of the newest Worker log |

The flag the table exists for: **Idle with cards waiting** means approved cards are sitting unclaimed, the Worker isn't
running, and nothing has been claimed for 15 min. If the Worker's last log says `WAITING: usage resets ...`, the row shows
Paused until that time instead. The rig is allowed to sleep, so a red rig row usually just means it's asleep.

The Alert column also shows **Needs Jake: ...** when the Worker logged a "needs Jake:" line in the last 12 h, so
anything waiting on you is on the row.

**Phone alerts:** the homebase watchdog pushes to your phone through the hub's `/api/notify` (web push) when either
row's alert changes to something new, and when the rig has been quiet for 30+ min while it has approved cards waiting.
A lone "Remote Control restarted" isn't pushed, since the watchdog already fixed it. If homebase itself goes down,
nothing can push; its row still turns red in Notion. Check the push path with
`powershell -File C:\Jarvis\watchdog\watchdog.ps1 -TestPush`.

**How the Remote Control check works:** the task's copy is found through Task Scheduler (the running task's process
and the `claude.exe` under it), because on homebase the task runs as S4U in session 0, where the watchdog can't read
command lines. A copy typed into a terminal is found by its command line. Remote Control counts as up if either is
running; if only a hand-started copy is up, the watchdog starts the task's copy alongside it and leaves the hand copy
alone. If nothing is up, it stops a task run that has no Remote Control under it (Task Scheduler otherwise refuses a
new start with 0x800710E0), starts the task, and if that fails starts the task's command directly. What it did is in
`logs\watchdog.log` and the row's Snapshot. Remote Control's own output is read from `C:\Jarvis\logs\remote-control.log`
(or the installer's debug log), and its last lines go on the row when a restart fails.

**Remote restart:** tick **Restart Remote Control** on a machine's Machine Health row (from your phone, or a cloud
Claude session does it through Notion) and that machine's next watchdog run, within 5 minutes, restarts Remote Control
even if its process still looks alive, then unticks the box. Use it when the Claude app shows the PC offline.

Test without writing to Notion: `powershell -File C:\Jarvis\watchdog\watchdog.ps1 -DryRun`

Install once per machine, in a normal PowerShell window at that machine:

```powershell
$t=[Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN','User'); if(!$t){$t=Read-Host 'GitHub token'; [Environment]::SetEnvironmentVariable('GITHUB_TASKS_TOKEN',$t,'User')}; irm -Headers @{Authorization="Bearer $t"; Accept='application/vnd.github.raw'} 'https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/install.ps1?ref=claude/eager-knuth-lakcxt' | iex
```

The one-time questions (trust the folder, enable Remote Control) have to be answered at the keyboard.
Claude Code has no way to pre-answer them, and without a terminal the server refuses to start.

## Gemini helper (free research and proofreading)

`helper/gemini_helper.py` hands research and proofreading to Gemini's free tier so Claude usage goes to real work.
Standard-library Python, no installs.

```powershell
setx GEMINI_API_KEY "<your AI Studio key>"   # once, then open a new terminal
python helper\gemini_helper.py proofread C:\Jarvis\outputs\draft.md -o C:\Jarvis\outputs\draft.review.md
python helper\gemini_helper.py research "free OCR libraries for Python"
```

- Proofread output lists problems with quoted fixes and ends READY or NEEDS FIXES; Claude applies the fixes.
- Research tries Google Search grounding first. The free key had no search quota on 2026-09-29, so it falls back
  to the model's own knowledge and marks unsure items as unverified; check links before relying on them.
- The free tier may use what you send for training, so don't send private documents.

## Heavy jobs and Remote Control drops

A big job on homebase (docker pulls in WSL, a model run) can starve Remote Control of memory, CPU or network, and the
connection drops even though the PC stays up. The watchdog raises Remote Control and the sessions it spawns to
AboveNormal priority on every run, puts free memory and WSL's share on the row, and alerts "Low memory" under 7% free.

WSL2 can take up to half the RAM by default. To cap it, run this once in PowerShell. It keeps an existing
`.wslconfig` and uses 40% of RAM and all but one core:

```powershell
$f="$env:USERPROFILE\.wslconfig"; if (Test-Path $f) { Get-Content $f } else { $gb=[Math]::Max(2,[Math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB*0.4)); $c=[Math]::Max(1,[Environment]::ProcessorCount-1); Set-Content $f "[wsl2]`nmemory=${gb}GB`nprocessors=$c" -Encoding ASCII; Get-Content $f }
```

The cap applies the next time WSL starts (`wsl --shutdown`, or a reboot), which also restarts the containers, so do
it when nothing is installing.

## No flashing windows

A scheduled task that starts `powershell.exe`, `cmd.exe` or `python.exe` flashes a console for a split second on
every run, even with `-WindowStyle Hidden`. `hide-task-windows.ps1` rewrites every `Jarvis*` task like that to start
through `wscript.exe` and a small `.vbs` in `C:\Jarvis\hidden\` that runs the same command with no window and waits
for it. Remote Control is left alone. Originals are saved in `C:\Jarvis\hidden\original-actions.json`; to undo, set
`$env:JARVIS_UNHIDE='1'` and run it again. Re-run it after re-running the installer or adding a Jarvis task.
