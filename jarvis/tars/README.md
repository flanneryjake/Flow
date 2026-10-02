# Jarvis TARS chat service (laptop)

`POST http://100.85.255.99:8790/chat {"text": "..."}` returns `{"reply", "humor", "filed"}`. `GET /health`, `GET /history?n=20`.

- Model: `jarvis-tars` (this Modelfile, built on `tars:v1`). Rebuild: `ollama create jarvis-tars -f Modelfile`.
- Memory: `C:\Jarvis\tars\history.jsonl` (every turn) + `summary.json` (rolling summary of turns older than the last 20).
- Facts: task/status/history questions read flanneryjake/jarvis-tasks (read-only) with `GITHUB_TASKS_TOKEN`.
- Filing: "start researching X" / "tell Claude X" files a `status:inbox` card (`for:claude` for Claude asks).
- Humor: "humor 40%" sets it (default 60), stored in `state.json`.
- Runs at logon from the scheduled task "Jarvis TARS" (pythonw, hidden); the laptop watchdog restarts it if it dies.
- Listens on 127.0.0.1:8790; `tailscale serve --bg --tcp 8790 tcp://127.0.0.1:8790` publishes it on the tailnet
  (tailscaled takes the inbound side, so no admin firewall rule is needed). The serve setting survives reboots.
