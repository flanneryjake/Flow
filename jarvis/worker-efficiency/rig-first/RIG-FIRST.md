# Rig-first one-time fixes (proposal #1588, chain #39), installed 10/03

Jake approved the plan on 10/03 ("install the Jake gate and the rig first patches"). Built and tested on copies of the
live rig files in Docker first (43/43 tests), then installed on the rig (DESKTOP-VLLDDM4).

| Card | What changed | Where |
|---|---|---|
| #856, #858 | `rig_anthropic_ok()` checks Ollama's Anthropic-style `POST /v1/messages`, not just `/api/tags`. New `python agent.py selftest-rig [model]` prints PASS/FAIL for both. With no model it uses the one already loaded (`/api/ps`), so it never swaps a model onto the GPU. | agent.py |
| #862 | `claude_cmd()` logs `claude cmd: <exact argv>` before every Claude launch (card run, fix pass, idle job). The prompt goes in on stdin, so no card text or secrets are logged. | agent.py |
| #851, #854 | Unattended card runs may launch the claude CLI: `Bash(claude *)` / `PowerShell(claude *)` are allowed, and `worker_script_guard.py` only passes `claude --version` or `claude -p ... --tools ""` (no tools). It refuses permission bypass, `--permission-mode`, `--allowedTools`, `--settings`, `--mcp-config`, `--add-dir`, plugins, agents, subcommands (`mcp`, `config`, `remote-control`, `update`) and interactive runs. | agent.py, worker_script_guard.py |
| #860 | The local-model flags, below. | this file |
| #861 | Launcher name corrected on #453 and #209. | card comments |

Not in this kit: #864/#865 (sharing the notes export needs Jake's PIN), #449 and the runner/model switch (owned by #1685,
"Running without Claude"), #852 (the qwen3.6-16g drafting test runs after that switch), #208, #451.

## Install / undo
```
python apply_rig_first.py --check     # every anchor found once + both files compile; writes nothing
python apply_rig_first.py --apply     # <file>.bak-<stamp>-rigfirst backups, keeps line endings
python apply_rig_first.py --revert    # newest -rigfirst backups back
python agent.py selftest-rig          # after the Worker restart
```
Tests (on copies): `RIGFIRST_DIR=<folder with agent.py + worker_script_guard.py> python -m pytest -q test_rig_first.py`.

## Local-model flags (#860): which flag, and where the Worker reads it

| Flag | Where | Read by | Effect |
|---|---|---|---|
| `JARVIS_LOCAL_MODEL` | user env (HKCU\Environment) | local_lane.py, fallback_lane.py, at Worker start | model for the local and fallback lanes; default `qwen3.6:35b`. Set to `qwen3.6-16g` on 10/03 by #449; it takes effect on the next Worker restart. |
| `JARVIS_RIG_MODEL` | user env | rig_ask.py | model for rig drafting asks; default `qwen3.6:35b`. |
| `JARVIS_LOCAL_LANE=off` | user env (or registry value) | local_lane.py | turns off "text-only free-tier cards go to the rig model first". |
| `JARVIS_FALLBACK_LANE=off` | user env | fallback_lane.py | turns off `model:local` / "while Claude is out" lane cards. |
| `claude-local.on` | file `%USERPROFILE%\JarvisAgent\claude-local.on` (the Worker's STATE_DIR) | claude_local.py (#208 kit) | the "flag" #451/#208 talk about: lets Claude Code run cards against the rig's Ollama while Claude is paused. **Not live yet**: claude_local.py is still only in the #208 work folder (not next to agent.py, not hooked into poll()), and the file does not exist. Jake creates it only after the #451 benchmark. Delete it to switch off. |

Env flags are read when the Worker process starts, so a change needs a Worker restart. Undo the model switch with
`reg delete HKCU\Environment /v JARVIS_LOCAL_MODEL /f` (same for `JARVIS_RIG_MODEL`).

## Launcher (#861)
The rig Worker starts from the Startup shortcut `Jarvis Agent.lnk` -> `wscript.exe run-agent.vbs` -> `pythonw.exe agent.py`
(all in `C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent`). `jarvis-autostart.ps1` does not exist on the rig.
