---
name: runbook
triggers: \b(stall\w*|stuck|idle|not working|broke\w*|crash\w*|down|offline|loop\w*|bounc\w*|reverted|vanished|error 153|nvidia|remote control|\brc\b|bom|comment flood)\b
summary: Runbook: check the usual suspects in order before guessing (real usage limit, paused hub, leftover flags, budget guard, roles rule, exiled cards); fix the real fault, log a training row.
---
- Worker idle with cards waiting, check in order: a real Claude limit vs a false pause; hub paused; C:\Jarvis\audit\fixes-running.flag left behind; budget guard; the roles rule (the 5060 only takes now/P0/P1 while the rig is awake); cards exiled, benched or off-phase.
- A card bouncing to Jake twice is a loop: park it for triage (looped label) and find what can be done without him. Never re-ask Jake the same thing.
- Waiting on another card: park it quietly as preapproved; it starts by itself when that card closes.
- An edit to agent.py, ghq.py or loopnet.py vanished after a restart: the self-edit guard reverted it. Approved fixes only, applied while the Worker is idle, then check after restart.
- Editing Python on Windows: use a UTF-8 Python write, never PowerShell 5.1 Set-Content (BOM). Back up first (.bak-<date>).
- Remote Control not back a minute after a reboot: replace the session.
- GPU crash (NVIDIA 153): look for a second model loading or a context-size change forcing a reload; keep one model resident.
- One comment per card run.
- After every fix: write a training row with training_intake.py.
