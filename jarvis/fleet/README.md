# Fleet control

Take one PC off the network without hurting the others, and keep every job covered when a PC goes down.

## What you see

Phone app, **System → Fleet**: one row per PC (Homebase, Rig, 5060 laptop) with a dot (green checked in, red
silent, grey paused or disconnected) and buttons:

| Button | What happens |
|---|---|
| **Pause** | The PC stays online but its Worker takes no new cards. The card it's on finishes normally. |
| **Disconnect** | Paused, finishes its running card, then leaves the tailnet (`tailscale down`). Internet and Remote Control stay up, so Claude can still reach it and it can always be brought back. |
| **Bring back** | Back to normal; rejoins the tailnet within 2 minutes. |

Before pausing or disconnecting, the panel shows what changes: which jobs move to which PC, which have nobody
else (they're shown in red, and you confirm before it goes ahead), cards only that PC can run, and a warning if
it's the PC serving the phone app. You pick **1 h**, **8 h** or **until I bring it back**; a timed one rejoins by itself.

Below the rows, **Who covers what** lists each job and the PC doing it now, with standbys.

## Where the state lives

In GitHub, not on any PC, so it survives whichever PC is off: the pinned **Fleet control** issue in
`flanneryjake/jarvis-tasks`. Its body holds each PC's mode; each PC rewrites its own heartbeat comment every
2 minutes. If the phone app itself is down (homebase off or disconnected), bring a PC back by editing that issue
on GitHub (change its `"mode"` to `"active"`), by asking Claude in the project, or from any PC:

```
python C:\Jarvis\fleet\fleet.py status
python C:\Jarvis\fleet\fleet.py plan rig          # what disconnecting the rig would do; changes nothing
python C:\Jarvis\fleet\fleet.py isolate rig --hours 8
python C:\Jarvis\fleet\fleet.py rejoin rig
```

## Redundancy

Jobs and their failover order are in `roles.json`:

| Job | Order | Notes |
|---|---|---|
| Phone app + approvals | homebase → laptop | The laptop only counts once a standby hub runs there (port 8770 answers). |
| Claude Worker | homebase → rig → laptop | `machine:any` cards go to whichever Worker is on. |
| Local model (Ollama) | rig → laptop | `fleet.py endpoint local-llm` returns the URL of whichever is up. |
| 3D printer | homebase | No standby. |

What keeps things running when a PC dies without warning:

- **Stranded cards come back.** A card still claimed by a PC that has been silent for 30 minutes, or that has
  left the tailnet, is handed back to the queue (`released`, the same as a Worker letting go), so another PC
  picks it up. Only the first live Worker does this, so a card is never released twice.
- **Nothing lives only on one PC.** Queue, health and fleet state are all GitHub issues.

## Install (once per PC)

After `install-ghq.ps1` (it sets `GITHUB_TASKS_TOKEN`), in a normal PowerShell window:

```powershell
irm https://raw.githubusercontent.com/flanneryjake/Flow/claude/eager-knuth-lakcxt/jarvis/fleet/install-fleet.ps1 | iex
```

It saves the files to `C:\Jarvis\fleet`, updates `C:\Jarvis\ghq\ghq.py`, registers the hidden **Jarvis Fleet**
task (`fleet.py tick` every 2 minutes, log in `C:\Jarvis\logs\fleet.log`) and runs one tick. Every PC starts active.

### Phone app (homebase hub)

Add to `server.py`'s request handler, before its own routes:

```python
sys.path.insert(0, r'C:\Jarvis\fleet')
import fleet_api
res = fleet_api.handle(method, path, body)   # body = parsed JSON or None
if res is not None:
    status, payload = res
    return send_json(status, payload)        # whatever the hub uses to reply with JSON
```

Then serve `C:\Jarvis\fleet\fleet-panel.js` with the app's static files and put
`<div id="fleet-panel"></div><script src="fleet-panel.js"></script>` in the System tab.

## Limits

- The Worker gate is in `ghq.ready()`, so it pauses Workers that read the GitHub queue (homebase since
  2026-09-30). A Worker still on Notion keeps taking Notion cards until it moves to GitHub.
- `tailscale down` / `up` run as the logged-in user. If Tailscale refuses (it needs that user to be its
  operator), the heartbeat line shows the tailnet still up and `fleet.log` has the error.
