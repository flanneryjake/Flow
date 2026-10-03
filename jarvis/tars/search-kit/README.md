# Card #924: web search + lookup tools on Tars (5060 laptop, 100.85.255.99)

## For Jake's morning (3 lines)
1. Kit is ready and tested on a copy of the TARS code (7/7 lookup tests pass; local-first search falls back to homebase in under 3 s; sunrise/sunset + weather tools give real numbers).
2. It isn't installed on Tars yet: the rig has no shell on Tars (ssh port 22 times out), so copy `kit\` to Tars and double-click `SETUP-TARS-SEARCH.bat` (no admin).
3. Before: Tars told you sunset was "6:58 PM ... can't browse the web". The real time is 6:23 PM, and the kit's local tool says 6:23 PM.

## What the kit does (on Tars)
| Step | File | Notes |
|---|---|---|
| SearXNG container on 127.0.0.1:8888 | setup_tars_search.py | Same as homebase: formats html+json, limiter off. `--restart unless-stopped` plus a Startup-folder `Jarvis SearXNG.cmd` that starts Docker Desktop and the container at logon. Settings/secret: `%USERPROFILE%\searxng\settings.yml` (secret generated there, never printed). |
| Local search first | apply_tars_tools.py -> `C:\Jarvis\tars\lookup.py` | `SEARX_URLS` = 127.0.0.1:8888, then 100.90.201.22:8888, 5 s each. Override with `JARVIS_SEARX_URLS`. Backups `*.bak-924`; `--rollback` undoes it. |
| Local tools | local_tools.py -> `C:\Jarvis\tars\` | Sunrise/sunset (computed offline, NOAA formula), weather (Open-Meteo, free, no key). They go into the chat context as LIVE FACTS before the model answers. Questions about other places go to web search. |
| Gemini lane | gemini_helper.py -> `C:\Jarvis\helper\` | Copied only if it's missing. Key = `GEMINI_API_KEY` user env var (same as homebase); the script only prints present/MISSING. lookup.py's PRIVATE_RE already keeps clinical/client/money text away from Gemini and the web. |
| Proof | tars-tools-result.txt | Local curl result, test run, TARS restart, plus a web question and a sunset question through POST /chat. |

The persona, the Modelfile and the lane names are unchanged (from Flow `claude/project-thread-16cc2j`), and there's no `ollama create`.

## Other lookup tools
- Done (trivial): time/date (already in context), sunrise/sunset, weather.
- Not wired: HA state read. It needs an HA long-lived token on Tars, and HA isn't confirmed up yet (cards #37/#41). Add `ha_state(entity)` once `HA_TOKEN` + URL exist.
- Not wired: calendar read. It needs a Google OAuth login on Tars (an outside account), so it's Jake's call. Read-only ICS "secret address" URL would be the cheap path.
- Not wired: unit conversion/math. The model handles it acceptably; low value.

## Untested here
The SearXNG install, the Startup entry and the TARS restart only run on Tars. If `apply_tars_tools` prints `lookup.py: MISSING`, copy `upstream\lookup.py` next to the live chat-service script first and rerun.

## Update 10/2 evening (after card #952)
Tars now runs card #952's Iron Man kit (`jarvis-ironman:latest`, lookup lane live). Its sunset answer was still wrong ("6:38 PM"; the real time is 6:23 PM), because the model misreads web results. This kit now:
- puts local sunrise/sunset/weather first inside `lookup.find()`, so those answers are exact;
- finds the live :8790 script itself (no more `C:\Jarvis\tars` guess);
- restarts it with kill and relaunch when there's no "Jarvis TARS" scheduled task.

Run `SETUP-TARS-SEARCH.bat` on Tars after #952's installer. If #952's installer is ever re-run, run this one again after it, because #952 copies its own lookup.py over the patched one.
