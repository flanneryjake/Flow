# Tars laptop Worker

Lets the RTX 5060 laptop (LAPTOP-4150EGRS) work as the rig's assistant: it takes the small, checkable jobs off
the Notion Tasks board and runs them on Tars (`tars:latest`, qwen3.5:9b on Ollama), and hands
anything bigger to the rig or to Claude.

## What it picks up
Cards with **Status = Approved**, **Auto-executable** ticked, **Claimed by** empty and **Machine = laptop**:
the same gate the other Workers use, with this machine's name. It never takes `Any`, `rig` or `homebase` cards.

## What it does with a card
Routing follows `kit/training/offload/offload-rules.md` (jarvis-outputs, branch `tars-kit`).

| Card | Result |
| --- | --- |
| Laptop-class: classify, tag, extract, reformat, triage, check, one-line summary or status | Tars does it. The result is added to the card's page and saved to `C:\Jarvis\outputs\laptop\`, and the card goes to **Done**. |
| Rig-class: long-form writing, code, planning, clinical content, over ~3,000 tokens in, or an answer over ~650 words | **Machine → rig**, back to Approved and unclaimed, with Tars' handoff note on the page and in Notes. It doesn't wake the rig for one job. |
| Claude-class: web, accounts, buying, posting, email, other PCs, or any card with an **Approval code** (PIN) | **Machine → Any**, back to Approved and unclaimed, so a Claude Worker takes it under its own PIN rules. |
| Fails twice in a row | Back to **Staged** with `NEEDS JAKE:` in Notes, and a `needs Jake:` line in the log that the Machine Health row shows. |

Tars only writes text. It runs no commands and has no tools, so everything it does is in the guardrails'
Free tier (local model, task cards, files in the work folders). A card asking for a PIN-tier action is passed on
by rule before the model sees it.

## Files on the laptop
- `%USERPROFILE%\JarvisAgent\laptop_worker.py`: the Worker. Polls every 60 s; `POST http://127.0.0.1:8791/wake` ends the wait early, `GET /health` shows its state.
- `%USERPROFILE%\JarvisAgent\laptop_watchdog.py`: starts Ollama and the Worker when they're down.
- `%USERPROFILE%\JarvisAgent\logs\agent.log`, `task-*.log`, `laptop-jobs.jsonl` (every job's input, route and output, for later training), `laptop-watchdog.log`.
- `C:\Jarvis\worker.heartbeat`: read by the existing Jarvis Watchdog for the laptop's Machine Health row.

## Scheduled task
**Jarvis Baby Watchdog** runs `pythonw.exe laptop_watchdog.py` every 5 min and at logon. pythonw has no console,
so no window appears. Install or update: `powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1`.

## Testing
- `python laptop_worker.py --dry-run` lists what it would claim and changes nothing.
- `python laptop_worker.py --once` runs one pass in the foreground (stop the background copy first, or it exits because the port is taken).
