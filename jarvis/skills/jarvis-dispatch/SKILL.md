---
name: jarvis-dispatch
description: Run a step on one of Jake's PCs (5060 homebase, rig, backup) yourself via Remote Control, or queue it as a jarvis-tasks card. Use instead of the old dispatch-to-homebase skill (Notion and the junk laptop are retired).
---

# Run something on Jake's PCs

**Jake's rule (said firmly, 2026-10-02): DO IT YOURSELF.** Never hand Jake files to hunt for or commands to run that Claude can run. Jake only does what truly needs his hands: a Windows UAC "Yes", something physical, or an account sign-in. When he must act, put the exact thing already open on his screen.

Machine facts (hosts, ports, env ids) are in the jarvis-pc-fleet skill. Short version:
- homebase = the 5060, LAPTOP-4150EGRS, 100.85.255.99, RC env_01CFfxoGXvEzHdV4iNcJ4R5V (default device, no consent card)
- rig = DESKTOP-VLLDDM4, 100.96.134.64, RC env_01BA2zUvsJrN9G6XUB7MoMDa (folder Claude)
- backup = junk laptop DESKTOP-5VE3C77, 100.90.201.22, RC env_018MR8zYNBuzbRRAuXcJ9za8

## Pick the lane
1. **Now, interactive** (Jake is waiting, or it needs judgment): Remote Control session on that PC.
2. **Unattended / can wait / long**: a card on flanneryjake/jarvis-tasks (the Worker on the PC runs it).
3. **Urgent but unattended**: a card with the `now` label (do-it-now lane: the Worker pauses its current card, runs this, then resumes).

## Lane 1: Remote Control
1. `list_devices` (or the equivalent tool) and check the PC is served. If tools for Remote Control are not available in this chat, use lane 2 and say so in one line.
2. **Reuse** a session already running on that PC if you have one. Each RC session is another Claude process on the PC: caps are 4 RC sessions per PC (2 on the backup) and 6 Claude sessions in all on the 5060. On 2026-10-02 a pileup of 13 app copies + 8 sessions filled the 5060's RAM and dropped Remote Control.
3. `start_rc_session` with a full, self-contained brief (what to do, paths, how to verify, what to report). Anchor it on Jake's message that asked for the work: the PC's auto-mode only acts on Jake's own words.
4. If the device is served but the session is not connected about 1 minute later, stop it and start a fresh one with the same brief. Don't wait across several of Jake's messages.
5. **Stop the session when the work is done.**

### What the PC's safety check refuses, and the workaround
- Widening its own permissions (settings.json allow rules): never possible from a session. Jake has to do it; ask once.
- Admin steps: write a self-elevating `.cmd` to the PC's Desktop and launch it; Jake only taps Yes on the UAC prompt. The 5060's hub, agent and waker run elevated, so their full restart goes this way (or C:\Jarvis\tools\restart-hub-agent.ps1, which works without admin).
- Scheduled tasks, downloading code from Flow, touching secrets, changing network routing: need a Jake line in the thread that names the exact action ("I approve" alone is refused on the backup).
- Rig: shutdown/restart commands and any path containing "token"/"key" are blocked. Jake restarts it, or uses the app's Power button + PIN.

### Never
- Never restart the hub or Worker on the backup (double-claims cards).
- Never force-restart the 5060 without Jake's OK. Never send monitor-off (SC_MONITORPOWER) to it.
- Never add the `restart-rc` label to a Health issue unless Remote Control is truly down: it kills live sessions.
- Never install Docker Desktop on the backup.

## Lane 2: a card
Create an issue in flanneryjake/jarvis-tasks (label rules in the jarvis-task-board skill):
- Title: short imperative ("Rig: rebuild the jarvis model with the new skills pack").
- Labels: `status:approved` (approved = it runs; use `status:staged` if Jake must approve), `machine:homebase|rig|backup|any`, `p0`/`p1`/`p2`, `type:*`, `project:*`; add `pin` for money, posting/sending, deleting personal data, accounts/credentials, exposing services or merging main.
- Body: small concrete steps (15 min no-progress limit), a verify step, and a stop condition. If it only waits on a file, say so: the Worker snoozes it instead of asking Jake.
- Medium/high-risk code goes through the rig's Docker sandbox first (jarvis-risk-and-sandbox skill).
- Wake the Worker: POST https://laptop-4150egrs.tail3bbcb8.ts.net:8443/wake (only reachable on the tailnet; otherwise the 60 s poll picks it up).

## Report
One line to Jake: what ran, where, and the result. If his hands are needed, say exactly which button, already on his screen.
