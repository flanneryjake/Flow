# No self-approval on the 5060 (Jake's rule, 10/04 00:39Z)

Jake's words: "Claude is the one who compiles the report and make it thorough as fuck. Every report that is reviewing the ai's is through Claude. I do not want an ai model evaluating itself as I want audits, quality control, and peer review"

## The rule

TARS or Jarvis may do a first pass. The final verdict on any AI's work is always Claude's, or Jake's. This covers:
- loop triage
- loop fixes
- card approvals and closes
- training-row approval
- grading
- review reports

Whenever Claude is off shift (Jarvis mode, nights), the item waits in the Claude queue. It never self-resolves.

## Step 1: Audit (read only, on the 5060)

Find every path where a local model's output becomes final without a verdict recorded as by=claude or by=jake. Cover these places:
- C:\Jarvis and C:\Users\Jake\JarvisAgent. Include agent.py, ghq.py, loopnet.py, needsjake.py, jake_gate.py, local_lane.py, fallback_lane.py, and the hub server.
- tars_server.py, including any tools TARS can call: close, approve, label or comment.
- training_intake.py, the BabyDatagen/TarsDatagen tasks, and anything that writes approved* or reviews*.jsonl.
- The re-eval or grading scripts, and the 7 AM, weekly and No-Ultron report builders. For the reports, check who writes the verdict lines.

Things to look for:
- A local verdict that closes a card, sets status:approved, applies a fix, or removes needs-claude-review.
- A local verdict that writes approved/training rows.
- A local verdict that fills in the "reviewed by" field of a report as the final reviewer.

## Step 2: Fix each hit

Back up every file before editing it, and touch fixes-running.flag before changing ghq or agent.

- The local result becomes a draft. Save it as first_pass with first_pass_by set to tars or jarvis.
- The card gets the labels for:claude and needs-claude-review, and goes to the Claude queue through jake_gate. Jarvis mode must not drain that queue. Only a Claude-brain run, or Jake, can clear it.
- Add a guard, final_verdict_allowed(by). It returns True only for "claude" or "jake". Call it everywhere a final action happens. Log any refusal to loopnet/jake-gate.jsonl with the event "self-approval-blocked".
- Reports show "first pass: X / final: Claude". Any item without a Claude verdict is listed as "awaiting Claude review", never as reviewed.

## Step 3: Test it

Write unit tests for each hit and run them on the 5060. Also run a dry run: feed one fake TARS verdict through each path and show that it lands in the Claude queue.

## Step 4: Report

- Write jarvis-outputs brains/no-self-approval-5060-2026-10-04.md with the hits (file:line), the fixes, the backups, the tests, and the undo steps.
- Push it and give the sha.
- Add training rows to C:\Jarvis\training\raw\ for each hole found. Use the format "I messed up by / I'll do better by".

Out of scope: no reboots, no new scheduled tasks, no rig changes. Rig and #1773 rule 1 go to the brains thread.
