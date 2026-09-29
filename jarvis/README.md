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
| Remote Control | Up, Restarted (was down, watchdog started it), Task missing |
| Worker | Working, Idle, **Idle with cards waiting**, Paused (usage limit), Unknown |
| Waiting cards | Approved + Auto-executable cards for this machine or Any with nothing in `Claimed by` |
| Last claim / Last claimed card | Newest `Claimed by` stamp this machine wrote on the Tasks board |
| Alert | Why the row isn't green |
| Snapshot | Full text: Remote Control, ports, Jarvis scheduled tasks, tail of the newest Worker log |

The flag the table exists for: **Idle with cards waiting** means approved cards are sitting unclaimed, the Worker isn't
running, and nothing has been claimed for 15 min. If the Worker's last log says `WAITING: usage resets ...`, the row shows
Paused until that time instead. The rig is allowed to sleep, so a red rig row usually just means it's asleep.

Test without writing to Notion: `powershell -File C:\Jarvis\watchdog\watchdog.ps1 -DryRun`

Install once per machine, in a normal PowerShell window at that machine:

```powershell
irm https://raw.githubusercontent.com/flanneryjake/Flow/claude/eager-knuth-lakcxt/jarvis/install.ps1 | iex
```

The one-time questions (trust the folder, enable Remote Control) have to be answered at the keyboard.
Claude Code has no way to pre-answer them, and without a terminal the server refuses to start.
