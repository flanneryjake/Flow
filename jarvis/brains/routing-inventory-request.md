# 5060 routing inventory (read-only)

Asked for by Jake on 10/04 at 00:34Z, in the 5060 audit thread: "5060: set brain to Jarvis and post the routing inventory Claude listed". This file is that list.

The "Jarvis and TARS brains" thread will use it to send each kind of task to the right machine, now that Jarvis (Qwen3-Coder-Next on the rig, about 26 min a card) is the brain.

Read only. Change nothing, restart nothing, and print no secrets.

1. **Scheduled tasks and services.** List every Jarvis/HB scheduled task and service on the 5060. Give each one's trigger or interval, its last run and last result, and whether it is enabled.
2. **Worker cards.** Count the cards the 5060 Worker took from 10/03 18:00 ET until now, grouped by type (label or project), with ok/fail counts and the average minutes per card.
3. **TARS.** Give the current load (Ollama ps, RAM, CPU) and the response time of a short /chat test on :8790.
4. **Routing.** List any card types the hub, Worker or agent already sends to TARS, Gemini or SearXNG instead of Claude or the rig. Give the file and line for each.
5. **Rig brain source.** Say whether the rig Worker reads the hub's /api/brain or its own local brain.json. Check the hub access log for GETs to /api/brain from 100.96.134.64.
6. **Junk laptop.** If it is reachable over the tailnet, give its service list (HA, Mosquitto, :8767, SearXNG :8888, AdGuard) with up/down for each.

Write the answer to jarvis-outputs `brains/routing-inventory-5060-2026-10-04.md`, push it, and report the commit sha.
