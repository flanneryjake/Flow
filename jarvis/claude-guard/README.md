# Claude guard

Keeps every PC from piling up Claude processes. On 10/02 the 5060 had 13 Claude app processes and 8 Claude Code
sessions open; they ate its 16 GB and Remote Control dropped.

`claude-guard.ps1` runs every 5 minutes on each PC (task **Jarvis Claude Guard**, elevated, no window) and:

1. **Counts** every Claude process by role: Remote Control server (`listener`), Remote Control sessions
   (project threads and phone sessions), `claude -p` runs (Worker cards and scripts), terminal sessions,
   desktop-app Code sessions, and the Claude desktop app (one window = one main process plus ~10 Electron helpers,
   which is why Task Manager shows "Claude (13)" for a single window).
2. **Cleans up** only what can't be doing useful work:
   - a `claude -p` run whose parent is gone, or older than 75 min (the Worker gives up at 45);
   - a Remote Control or desktop Code session whose server or app is gone, after two idle checks in a row;
   - a second Remote Control server with no sessions under it, seen twice in a row. A server with live sessions
     is never stopped, and the Remote Control task's own copy is the one kept.
   Terminal sessions, the desktop app and anything with live sessions are reported, never stopped.
3. **Shows** the counts in the phone app (System > Fleet, one line per PC) through its comment on the pinned
   **Fleet control** issue, and keeps `status.json`, `history.csv` and `snapshots\` in `C:\Jarvis\claude-guard`.
4. **Alerts** when a count is over its cap: a phone push and a comment on the PC's Health issue with the full
   process list. Once per new problem, again every 3 h while it lasts, and a note when it clears.

## Caps

`config.json` (kept across reinstalls). Defaults: 1 Remote Control server, 4 Remote Control sessions, 2 `-p`
runs, 2 terminal sessions, 3 desktop Code sessions, 1 desktop window, 8 sessions in all (6 on the 5060), and
Claude using at most 35% of RAM. The backup gets 2 Remote Control sessions.

## Install

Per PC, double-click **Install Claude guard.cmd** (asks for admin). Without admin it installs a limited version
that runs while the user is logged in and can't see elevated processes. On homebase it also updates the Fleet
panel and restarts the hub. Test without Windows: `pwsh tests/test-claude-guard.ps1`.

## Remote Control sessions from project threads

Each project thread that starts a Remote Control session adds one Claude Code process to that PC until the
session ends. Threads should reuse the session already running on a PC, and end theirs when the work is done.
The guard's `rc_sessions` cap is the backstop: over it, Jake gets a push naming the PC.
