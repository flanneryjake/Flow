# Cross-connect: every machine can check and fix the others

Proposal: https://claude.ai/code/artifact/b19b451c-e442-408e-b6d4-77a8cbb1ea20 (Jake, 2026-10-03).

The same node agent runs on every machine (5060, rig, junk laptop, later Hal9000). It listens on the tailnet only
(port 8799) and answers a fixed list of commands from its peers. Jake approves `approvals.json` once, so no single
command needs his words again. Deleting, spending, posting, and widening `approvals.json` still need his PIN.

| File | What it is |
| --- | --- |
| `node_agent.py` | The agent. `GET /health` (no auth), `POST /cmd` (signed, approved, rate limited, audited) |
| `client.py` | Sign and send one command: `python client.py junk restart_service --name searxng` |
| `approvals.json` | Who may run what on whom, and how often. Default deny |
| `examples/node.*.json` | Per-machine config: tailnet bind address, services, wake routes, fixes, logs |
| `test_node_agent.py` | 20 tests; the command runner is a recorder, so nothing is ever restarted |
| `sandbox-test.cmd` | The Docker sandbox run on the rig (Jake's rule for medium-risk code) |

## Commands

| Command | Does | Default limit per target |
| --- | --- | --- |
| `status` | services up or down (port checks), RAM, disk, uptime, paused | none |
| `logs {name}` | last 200 lines of a log listed in the config | none |
| `restart_service {name}` | ends and re-runs the service's scheduled task (or restarts a Windows service) | 10 min apart, 12 a day |
| `run_fix {name}` | runs a fix script listed in the config, only while its sha256 matches | 10 min apart, 10 a day |
| `wake {peer}` | the configured tailnet wake URL, or Wake-on-LAN | 5 min apart, 24 a day |
| `restart_pc` | `shutdown /r /t 60`, never `/f`; refused while the busy flag exists | 30 min apart, 4 a day |

Only commands that actually ran count toward a limit. Past the daily limit the agent stops and Jake decides.

## Safety

- Requests are signed with the caller's key (HMAC-SHA256), expire after 60 s, and are single-use (nonce).
  Keys live only in `C:\Jarvis\secrets` (`crossconnect-self.json` on the caller, `crossconnect-peers.json` on the
  receiver). They are never logged, returned or sent between machines.
- The agent refuses to start unless `bind` is the machine's 100.x tailnet address.
- A machine paused in fleet control (`C:\Jarvis\fleet\paused.flag`) or with `off.txt` beside the agent answers
  `status` only.
- Every call, allowed or refused, is a line in `state\audit.log`.

## Fits with the rest of the system (checked 2026-10-03)

- **Fleet control** (`jarvis/fleet`): the agent obeys fleet's paused flag; fleet still owns pause, isolate and
  card failover. An isolated machine is off the tailnet, so its agent is unreachable by design.
- **Rig power rules** (`rig-idle-shutdown.ps1`, wake batching): a sleeping rig is normal, not "down". Peer
  self-heal (phase 5) must never wake the rig except under the existing wake rules. `restart_pc` on the rig is
  refused while its busy flag exists.
- **Board wipe** (`C:\Jarvis\boardwipe` on the 5060): keeps its own scheduled tasks. In phase 5 it switches to
  calling the agents, which also gives it the junk laptop restart it skips today.
- **#1571, Remote Control starting itself after reboot**: owned by the audit thread. The agent does not start or
  stop Remote Control; a `restart_service` entry for it is added only once #1571 says how RC runs on each PC.
- **Claude guard**: the agent is plain Python, not a Claude process, so the guard's counts are unaffected.
- **Loop net breaker and E5** (10 writes per card per hour): the phase 4 card lane edits one report comment per
  card instead of posting new ones.
- **Workers** (`agent.py`, `ghq.ready()`): phase 4 command cards use their own label and are never
  `status:approved`, so Workers don't claim them. Coordinate the label with the Worker loop thread before then.
- **Claude day shift (#1548)**: the agents need no Claude, so they keep working at night.
- **Ports**: 8799 is free on all three machines. Junk laptop's tailscale serve (443, 8888, 8767) and portproxy
  (8123, 8888, 1883) are untouched; its HA status check uses host 100.90.201.22.
- **Rig self-edit guard**: fix scripts may not touch `agent.py`, `ghq.py`, `loopnet.py`, `autonomy\*.py`,
  `worker-settings.json` or the guardrails.

## Phases

1. Build and test (this folder). Jake approves `approvals.json` with one yes.
2. 5060 and rig: install, keys, status and `restart_service` only.
3. Junk laptop: install; `restart_pc` and `wake` between all three; the 8767 relay folds in.
4. Command cards, app buttons and Tars voice commands call the agents.
5. Self-heal and the 72-hour board wipe on the agents; Hal9000 joins as referee.
