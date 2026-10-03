---
name: jarvis-pc-fleet
description: Facts about Jake's home lab (5060 homebase, rig, backup laptop, Pi): hostnames, tailnet IPs/URLs, ports, Remote Control ids, which model runs where, and what never to do to each PC.
---

# Jake's PC fleet (Apartment Jarvis)

Use this whenever a question or task touches one of Jake's computers, the tailnet, the phone app, or the local AI models. Facts as of 2026-10-03. If a fact here conflicts with what you see live, trust the live check and tell Jake what changed.

## Machines

| Name | What it is | Hostname | Tailnet IP | Remote Control device |
|---|---|---|---|---|
| **homebase** ("the 5060", model **Tars**) | ASUS laptop with an RTX 5060 (8 GB VRAM, 16 GB RAM). MAIN homebase since 2026-10-02. | LAPTOP-4150EGRS (user Jake) | 100.85.255.99 | env_01CFfxoGXvEzHdV4iNcJ4R5V (DEFAULT device, starts with no consent card). The older env_01HKi1xTuxLZHspGRbossD5c is dead. |
| **rig** (model **Jarvis**) | Big desktop, 16 GB GPU, Docker Desktop (WSL2) sandbox. Does heavy drafting and research. | DESKTOP-VLLDDM4 (user Jake) | 100.96.134.64 | env_01BA2zUvsJrN9G6XUB7MoMDa, folder "Claude" (%USERPROFILE%\Desktop\Claude) |
| **backup** ("Junky POS", the junk laptop, old homebase) | HP 17, 7.9 GB RAM. Backup only. | DESKTOP-5VE3C77 (user PEOL) | 100.90.201.22 | env_018MR8zYNBuzbRRAuXcJ9za8 "DESKTOP-5VE3C77 (jarvis)" |
| **Hal9000** | Raspberry Pi, boots from M.2 over USB; flash planned weekend of 2026-10-03 from the rig (Flow jarvis/pi). Tailnet name hal9000. | | | |
| **Frank** | A PC Jake is still building. | | | |

Tailnet domain: `tail3bbcb8.ts.net`. Tailscale key expiry is off on all machines. DNS: AdGuard on the backup (100.90.201.22) first, then the rig (100.96.134.64); no Cloudflare.

## Services on homebase (the 5060)
- Phone app / hub: https://laptop-4150egrs.tail3bbcb8.ts.net (443 -> :8770). Code C:\Users\Jake\ClaudeCode\JarvisKit\jarvis-hub. Tabs Home / Inbox / Jarvis / System; PIN-gated actions.
- Worker agent :8780, served at https://laptop-4150egrs.tail3bbcb8.ts.net:8443 (POST /wake, /now, GET /card/<n>).
- Waker :8765 (https :8766). Print service :8795. Printer camera https :8081.
- **Tars chat**: POST http://100.85.255.99:8790/chat {"text": ...}, GET /health. Ollama model `tars:latest` (qwen3.5 9B fine-tune "baby-jarvis"); callers must send "think": false. Code C:\Jarvis\tars.
- Discord bot (Jarvis#0102 in "The Lobsta Shack 2.0") admin :8796 local only; ON/OFF switch in app System > Settings (PIN). It is OFF unless Jake turns it on.
- Zoo-glass friends page :8781 (Funnel :10000, on STANDBY: C:\Jarvis\zoo-glass\TURN-ON.cmd / TURN-OFF.cmd). Fieldwork site tailnet-only :10001. Never enable Funnel on 443 or 8443.
- Rollback for the homebase move: C:\JarvisStaging\rollback.ps1. Hub+agent restart without admin: C:\Jarvis\tools\restart-hub-agent.ps1.
- Hub, agent and waker run ELEVATED: a full restart needs admin (see the jarvis-dispatch skill for the self-elevating .cmd trick).

## Services on the rig
- Ollama model `jarvis` (qwen3.6:35b, Iron Man JARVIS persona). Local lane routes free, text-only, non-clinical cards to it first (off switch: user env JARVIS_LOCAL_LANE=off).
- Worker agent: Desktop\Claude\jarvis-rig\jarvis-agent\agent.py (Startup "Jarvis Agent.lnk").
- "Hey Jarvis" mic listener C:\Jarvis\rig-voice (:8796 via tailscale serve) -> Tars -> Kitchen Echo.
- Docker sandbox: C:\Jarvis\sandbox\sandbox.ps1 (see jarvis-risk-and-sandbox skill).
- Idle shutdown after 10 min idle: C:\ProgramData\JarvisIdle (off.txt disables). Wake via the backup's relay http://desktop-5ve3c77.tail3bbcb8.ts.net:8767/wake/rig. Wake only for 60+ min of queued work, a Jake tap, or a P0; 60-min cooldown after a shutdown.
- Claude settings on the rig block shutdown/restart commands and any path containing "token"/"key". Jake restarts it himself or with the app's Power button + PIN.

## Services on the backup (junk laptop)
- Home Assistant (WSL2 Docker): http://100.90.201.22:8123, https://desktop-5ve3c77.tail3bbcb8.ts.net. Mosquitto, voice stack (whisper/piper/openwakeword), SearXNG :8888 (Tars web search), AdGuard DNS, the rig wake relay :8767 (the rig's cable is on it), watchdog, fleet.
- Echos: media_player.master_bedroom, media_player.kitchen; "Everywhere" group. HA pushes a home snapshot to Tars POST /ha every 2 min.
- NEVER restart the hub or Worker on the backup (they would double-claim cards). Never install Docker Desktop there. Jake does not want to touch it; its Claude auto-mode only acts on a Jake line that names the exact action.

## Repos and accounts
- github.com/flanneryjake/Flow (PRIVATE): all jarvis/ code lives on the installer branch `claude/eager-knuth-lakcxt`; `main` has no jarvis/ folder. Never open a PR whose head is the installer branch (auto-delete would remove it). Download files with the contents API: `https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/<path>?ref=claude/eager-knuth-lakcxt` plus headers `Accept: application/vnd.github.raw` and `Authorization: Bearer <GITHUB_TASKS_TOKEN>`. raw.githubusercontent.com links 404.
- flanneryjake/jarvis-tasks (private): the task board (see jarvis-task-board skill). Health issues: homebase #1, rig #2, backup #889. Fleet control #352.
- flanneryjake/jarvis-outputs (private): finished outputs; branch pc-code/<pc> holds nightly code snapshots.
- The PCs use the GitHub account clauderigassist-cell (user env var GITHUB_TASKS_TOKEN). Claude sessions use Jake's account. API limit is 5,000/hr per account.
- On Jake's PCs, .ps1 files and `irm | iex` usually don't run; plain pastes and .cmd files do.

## Hard rules
- The 5060 is Modern Standby: never post SC_MONITORPOWER or "monitor off" there (it sleeps and drops the tailnet). Screens-off uses blackout.ps1. Never force-restart it without Jake's OK.
- Secrets never go in chat, memory, repos or skills. Jake saves a secret as a .txt on the rig desktop with no "key"/"token" in the file name.
- Jake never reviews anything ON a PC: deliver to his phone (attachments, Drive, GitHub) or the rig.
