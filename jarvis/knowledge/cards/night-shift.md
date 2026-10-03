---
name: night-shift
triggers: \b(claude (?:is )?(?:out|offline|down|gone|asleep)|night ?shift|headless|without claude|jarvis mode|brain (?:switch|mode)|take over|hand it back|usage limit|limit reached)\b
summary: Night shift: when Claude is out (usage limit or Jake flips it), Jarvis runs the cards on the rig with local-mode rules; anything risky waits on a branch for Claude's review.
---
- Brain modes: claude, jarvis, or auto. Auto means Claude first, then Jarvis on a REAL Claude usage-limit error, checking hourly to hand back.
- A real limit is Claude Code's own limit message on the first line of its output. "Rate limit" inside a normal answer is NOT a Claude limit; keep working.
- Jake can flip it: app System tab toggle, header chip, voice ("Jarvis, take over" / "Hand it back to Claude").
- Local-mode rules for Jarvis: never push or merge to main; code goes onto a branch labelled needs-claude-review after a Docker sandbox test; no spending, posting, sending outside or deleting; never edit live Worker, hub or guard code.
- Routines keep running on the rig in Jarvis mode: 7 AM report, 3 PM Waiting on Jake, 8:56 PM nightly phase cards, email-reply pickup every 30 min, 2:30 AM brain snapshot. No Gmail app password = report goes to the app.
- Claude's day shift is 6 AM to 10 PM ET. Overnight, only an emergency (p0) wakes Claude.
- Keep one big model on the rig's graphics card at a time. Loading a second one causes crashes (NVIDIA error 153).
