# Jarvis TARS chat service (laptop)

`POST http://100.85.255.99:8790/chat {"text": "..."}` returns `{"reply", "humor", "filed"}`. `GET /health`, `GET /history?n=20`.

- Model: `jarvis-tars` (this Modelfile, built on `baby-jarvis`). Rebuild: `ollama create jarvis-tars -f Modelfile`.
- Memory: `C:\Jarvis\tars\history.jsonl` (every turn) + `summary.json` (rolling summary of turns older than the last 20).
- Facts: task/status/history questions read flanneryjake/jarvis-tasks (read-only) with `GITHUB_TASKS_TOKEN`.
- Filing: "start researching X" / "tell Claude X" files a `status:inbox` card (`for:claude` for Claude asks).
- Iron Man persona: `jarvis-ironman` (`Modelfile.ironman`, FROM tars:v1). Rebuild: `ollama create jarvis-ironman -f Modelfile.ironman`.
  Turns and memory older than `JARVIS_PERSONA_SINCE` (old Boston voice) are never fed to the model; the files stay as they are.
- Live facts (`live.py`): sunrise/sunset are computed for Plymouth, MA; weather comes from the National Weather Service
  (api.weather.gov, free, no key). Both go into the context as a LIVE block, so no web search is needed.
- Lookups (`lookup.py`): `LOOKUP:` runs SearXNG (`JARVIS_SEARX_URL`) and reads the top two pages, then Gemini, then
  `claude -p`; `ASK_CLAUDE:` goes to Claude. Spoken replies are capped at 3 sentences (Claude answers excepted).
- Home (`home.py`): Home Assistant (packages/tars_home.yaml on the backup laptop) pushes a snapshot to `POST /ha`
  every 2 min (alarms, what's playing, listening switch, lights) plus its private webhook URL, kept in `ha.json`.
  "play jazz", "lights off", "set an alarm for 6", "... in the bedroom" run as Alexa voice commands on that Echo;
  buying, ordering, calling and messaging are refused. No HA token lives on this laptop.
- Humor: "humor 40%" sets it (default 60), stored in `state.json`.
- Runs at logon from the scheduled task "Jarvis TARS" (pythonw, hidden); the laptop watchdog restarts it if it dies.
- Listens on 127.0.0.1:8790; `tailscale serve --bg --tcp 8790 tcp://127.0.0.1:8790` publishes it on the tailnet
  (tailscaled takes the inbound side, so no admin firewall rule is needed). The serve setting survives reboots.
