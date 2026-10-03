---
name: jarvis-health-triage
description: Diagnose and fix a Jarvis PC or Worker that looks down, stalled, red or offline: Health issues, Remote Control drops, Claude pileups, usage and GitHub limits, Modern Standby sleep.
---

# Jarvis health triage

Jake wants audits EXTREMELY thorough: fix root causes, verify end to end, and never hand him a list that repeats an issue that was already reported and not fixed.

## Where health lives
- flanneryjake/jarvis-tasks issues labelled `health`: **Health: homebase #1** (the 5060), **rig #2**, **backup #889**. Each PC's watchdog (every 5 min) edits its issue. Red/offline = last check-in over 15 min old. A red rig is often just the rig asleep (allowed: it idle-shuts down after 10 min).
- Fleet control issue #352: JSON mode per PC (active / paused / isolated, optional `until`) plus heartbeat comments and a `jarvis:claudewatch` comment with Claude process counts.
- Phone app System tab (https://laptop-4150egrs.tail3bbcb8.ts.net): Fleet, health alerts, Restart buttons (Restart drains first; POST /power/cancel aborts).
- Local files: C:\Jarvis\worker.heartbeat, agent.log, C:\Jarvis\logs\remote-control.log, C:\Jarvis\claude-guard\status.json + history.csv, C:\ProgramData\JarvisIdle\idle.log + last-shutdown.txt (rig).

## Check in this order
1. **Is it a usage limit?** Search agent.log for "session limit" / "usage resets". Only a real Claude CLI usage-limit error pauses Claude cards; never add estimate-based holds. The Worker resumes on its own after the reset.
2. **GitHub rate limit?** 403 "secondary rate limit" or X-RateLimit-Remaining near 0 (read headers on a repo call; /rate_limit misreports). Cause seen 10/02: ~470 comments in 50 min from a rig guard bouncing approvals. Fixes live in ghq: shared cooldown C:\ProgramData\Jarvis\github-cooldown.json, comment outbox, 6 h same-comment skip, 1 s write gap, idempotent approve, max 3 runs per card per day.
3. **Remote Control down?** Check the RC log and the device in list_devices. If a session is parked after a reboot, replace it after ~1 min. Only if RC is truly down: add the `restart-rc` label to that PC's Health issue (the watchdog restarts RC within 5 min). That label KILLS live sessions, so never use it on a working RC.
4. **Too many Claude processes?** "Jarvis Claude Guard" task (every 5 min, Flow jarvis/claude-guard) stops only orphans and warns over caps (4 RC sessions per PC, 2 on backup, 6 sessions total on the 5060). Task Manager "Claude (13)" can be ONE desktop app window (Electron helpers). Never kill a listener that has live sessions.
5. **5060 vanished from the tailnet?** It is Modern Standby: monitor-off, lid, power button or sleep = standby, and all programs pause. Keep-awake task "Jarvis Keep Awake" + AC never-sleep in all 4 ASUS plans. Never use SC_MONITORPOWER there; use blackout.ps1.
6. **Worker idle with cards waiting?** Check its IDLE-REASON line: needs-jake only, wrong machine, fleet paused/isolated, rig downtime window (C:\Jarvis\downtime-rig.json), rig hub_paused() (an unreachable hub = PAUSED on purpose, do not make it fail-open), or cards deferred to the rig while the rig heartbeat is fresh (worker roles: [rig, homebase]).
7. **Looping cards?** A card re-run with nothing new. The Worker must use ghq.send_back() (triage, then snooze on file/card/machine/time); after 3 snoozes it goes to Jake once.
8. **agent.py edits not taking effect?** The pythonw process must restart between cards to load edits.

## Fix rules
- Fix it yourself through Remote Control (jarvis-dispatch skill). Back up every file you change (`.bak-YYYYMMDD-<reason>`).
- Don't restart the hub or Worker on the backup. Don't force-restart the 5060.
- Risky code: rig Docker sandbox first (jarvis-risk-and-sandbox skill).
- Report: what was wrong (root cause), what you changed, how you verified it. Anything left for Jake goes on his To-Do page (jake-todo-list skill), not as a new list.
