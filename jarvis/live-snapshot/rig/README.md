# Live Worker snapshot - rig (DESKTOP-VLLDDM4), taken 2026-10-03

Read-only copies of the code the rig Worker runs, for the Worker efficiency review (E1-E10, #1516-#1525, #1547).
Secret scan: no hard-coded tokens/keys/passwords/PINs found (they come from env vars and files on disk), so nothing needed <REDACTED>.

Launch: no 'Jarvis Worker' scheduled task exists on the rig. agent.py is started by agent/run-agent.vbs (pythonw, hidden); it listens on :8790 (pid 33508 at snapshot time). It imports ghq.py from C:\Jarvis\ghq when JARVIS_QUEUE=github; ghq.py imports loopnet.py and needsjake.py. The #427 budget patch is budget_guard.py; the fallback/idle lane is fallback_lane.py + local_lane.py.

| File | Source path | Size (bytes) | Last modified |
|---|---|---|---|
| agent/agent.py | C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent\agent.py | 126990 | 2026-10-03 12:52 |
| agent/budget_guard.py | C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent\budget_guard.py | 29591 | 2026-10-03 12:52 |
| agent/fallback_lane.py | C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent\fallback_lane.py | 18324 | 2026-10-03 01:21 |
| agent/local_lane.py | C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent\local_lane.py | 12976 | 2026-10-02 16:04 |
| agent/rig_ask.py | C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent\rig_ask.py | 1830 | 2026-10-02 14:53 |
| agent/run-agent.vbs | C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent\run-agent.vbs | 183 | 2026-09-28 11:33 |
| agent/sandbox_gate.py | C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent\sandbox_gate.py | 15790 | 2026-10-02 17:04 |
| agent/worker_script_guard.py | C:\Users\Jake\Desktop\Claude\jarvis-rig\jarvis-agent\worker_script_guard.py | 5887 | 2026-10-02 17:14 |
| ghq/README.md | C:\Jarvis\ghq\README.md | 5097 | 2026-10-01 16:10 |
| ghq/ghq.py | C:\Jarvis\ghq\ghq.py | 76485 | 2026-10-03 12:19 |
| ghq/loopnet.py | C:\Jarvis\ghq\loopnet.py | 24990 | 2026-10-03 09:34 |
| ghq/needsjake.py | C:\Jarvis\ghq\needsjake.py | 17951 | 2026-10-02 23:25 |
| ghq/test_loopnet.py | C:\Jarvis\ghq\test_loopnet.py | 12452 | 2026-10-02 23:25 |
| ghq/test_needsjake.py | C:\Jarvis\ghq\test_needsjake.py | 6603 | 2026-10-02 23:25 |
