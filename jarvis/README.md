# Jarvis Always-On

Makes homebase and the rig reachable from Jake's phone and from cloud Claude sessions, and makes failures visible.

Why this exists: cloud Claude sessions can't join Tailscale, so they can never connect *in* to a home PC.
Everything here has the home PC connect *out* instead:

- **Remote Control** (`claude remote-control`) dials out to claude.ai, so the PC shows up as a session in the
  Claude app and can take work from the phone or the cloud.
- **Watchdog** (every 5 min) restarts Remote Control when it dies (it exits after ~10 min offline) and writes a
  health snapshot (Remote Control, hub ports, Jarvis scheduled tasks, tail of the newest Worker log) to the
  machine's `💓 Heartbeat` card in the Notion Tasks board, so a stall shows up in Notion instead of going silent.

Install once per machine, in a normal PowerShell window at that machine:

```powershell
irm https://raw.githubusercontent.com/flanneryjake/Flow/claude/eager-knuth-lakcxt/jarvis/install.ps1 | iex
```

The one-time questions (trust the folder, enable Remote Control) have to be answered at the keyboard.
Claude Code has no way to pre-answer them, and without a terminal the server refuses to start.
