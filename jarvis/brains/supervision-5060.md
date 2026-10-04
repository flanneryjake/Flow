# 5060 side of the supervision model (Jake 10/04 00:39Z)

The spec is in jarvis-outputs, `brains/supervision.md` (commit 8cdcc07). This file covers only the 5060's part. Back up each file before you edit it.

## 1. Hub: accept the rig's agenda

- Change `/api/state/marker` so it also accepts the routine `supervision`.
- With it, accept one extra file at exactly `state/supervision/jarvis-<YYYY-MM-DD>.jsonl`. No other path.
- Keep everything else the same:
  - only the rig can post
  - files are capped at 256 KB
  - files are secret-scanned
  - the publisher pushes at most once a minute
- Every line must parse as JSON, and `kind` must be one of question, stuck, resource, unsure, learned or answer. If either check fails, reject the post with a 400.

## 2. TARS: write its own agenda

- TARS appends its items to `C:\Jarvis\state\supervision\tars-<YYYY-MM-DD>.jsonl`, using the spec's format with `model: "tars"`.
- No more than 5 items may be open at once. When a 6th comes in, it is carried, not dropped.
- TARS adds an item when:
  - a /chat turn ends with "I'm not sure",
  - a card is stuck, or
  - Jake asks it something it can't answer.
- Use a small helper, `supervision.py add <kind> <text> [--card N]`. TARS's tool layer and the Worker can both call it.
- The existing brain.json publisher pushes the file to jarvis-outputs `state/supervision/`. Use the same once-a-minute, on-change rule and the same secret scan.

## 3. No self-review

- TARS never writes `answer` lines and never marks its own items answered.
- Only Claude answers, by appending `{"kind":"answer","re":N,"text":...}`.

## Done when

- A test POST from the rig of a jarvis agenda line lands in jarvis-outputs.
- One TARS test item lands in `tars-<date>.jsonl` in jarvis-outputs.
- A bad path or bad kind is rejected.
- Unit tests pass.
- A short report goes to jarvis-outputs `brains/supervision-5060-2026-10-04.md`.

Limits: no new scheduled tasks and no reboots. Restart only the hub and TARS.
