# Jarvis guardrails

What Jarvis (the Workers, Claude sessions on homebase and the rig, the local model and the Gemini helper) may do
without Jake. Rewritten 2026-09-30 on Jake's direction: trust the system on routine work, ask once instead of over
and over, and let Claude feed training data to Jarvis without per-item approval.

`policy.json` is the machine-readable copy. The approval hook and the Worker read it; if the two ever disagree,
this file wins and `policy.json` gets fixed.

## The four tiers

| Tier | What happens | Jake's part |
| --- | --- | --- |
| **Free** | Runs with no prompt. | Nothing. |
| **Free + logged** | Runs with no prompt, written to a log that the 7 AM report summarizes, and can be undone. | Skim the report; undo if something looks wrong. |
| **Ask once** | Asks the first time for each *kind* of action, then remembers the answer. | One tap per kind, ever (until he revokes it). |
| **PIN** | Asks every time, with the PIN. | Approves each one. |

When an action fits more than one tier, the stricter one applies. Anything not listed is **Ask once**.

### Free
- Reading any file in the Jarvis work folders, the Flow repo, and the project's shared files.
- Writing and editing inside the work folders: `C:\Jarvis\jobs`, `C:\Jarvis\outputs`, `C:\Jarvis\reports`,
  `C:\Jarvis\work`, and the rig's `Desktop\Claude` folder.
- Drafting on the rig's local model. Research and proofreading on the Gemini helper (non-private text only, see
  hard stops).
- Web searches and reading public pages.
- Moving a task card/issue through its states, writing its log, creating follow-up cards.
- Restarting Jarvis's own processes (Worker, Remote Control, watchdog, hub) when they are stuck.

### Free + logged
- **Training data.** Adding, updating or reorganizing anything under `C:\Jarvis\training`, through
  `training_intake.py` (see below). Every batch is logged with its source and can be reverted with one command.
- Rebuilding the `jarvis-fc` model from the training set (`ollama create jarvis-fc ...`). The previous build is
  kept under a dated tag so a bad build can be rolled back.
- Pushing to `claude/*` branches of `flanneryjake/Flow` and opening pull requests (never merging to `main`).
- Deleting files that Jarvis itself created inside the work folders or the training folder, after a backup copy
  is written (`.bak-<date>` or the intake snapshot).

### Ask once (remembered per kind)
- Creating or changing scheduled tasks, startup entries, or services.
- Installing software or packages, or running code downloaded from the internet.
- Changing network, firewall, Tailscale, power or Windows settings.
- Editing files outside the work and training folders.
- Anything not listed in this file.

"Remembered" means the hook stores the approval in `C:\Jarvis\guardrails\remembered.json` keyed by kind
(for example `scheduled-task` or `pip-install`). Jake can clear a kind from the phone app or by deleting its line.

### PIN (every time)
- Spending money or starting anything that bills (subscriptions, paid API tiers, purchases, Shopify orders).
- Posting publicly or sending anything to people outside the project: email, social posts, publishing a store
  product, messaging a client.
- Deleting things Jarvis didn't create, bulk deletes (more than 20 files), or deleting anything without a backup.
- Keys, tokens, passwords, account and security settings (including disabling Defender or Tailscale key expiry).
- Merging into `main` of any repository.

## Training data: the free lane

Claude, the Worker and the local model may add training data to Jarvis without asking, as long as it goes
through the intake script:

```powershell
python C:\Jarvis\guardrails\training_intake.py add <file-or-folder> --topic fc-curriculum --source "PubMed summary, 2026-09-30"
python C:\Jarvis\guardrails\training_intake.py list            # recent batches
python C:\Jarvis\guardrails\training_intake.py revert <batch>  # undo one batch
```

The script copies the files into `C:\Jarvis\training\<topic>\`, snapshots anything it overwrites, records each
file's source and checksum in `C:\Jarvis\training\_log\intake.jsonl`, and refuses a batch that breaks a hard stop
below. The 7 AM report lists the day's batches on page 1, so Jake sees what went in without approving it.

What counts as good training data: research notes and summaries Claude or Gemini produced, Jake's own curriculum
and notes, public documentation, and Q&A pairs built from those. New research material is expected and welcome.

## Hard stops (no tier unlocks these)
- **Clinical content never goes to Gemini** or any other outside service. It stays on homebase and the rig.
- **No patient or client identifying information** in training data (names with health details, dates of birth,
  record numbers, contact details). The intake script scans for the obvious patterns and refuses the batch.
- **No secrets** in training data or logs: API keys, tokens, passwords. The intake script refuses those too.
- No whole copyrighted books or paywalled articles copied verbatim; summaries, notes and short quotes are fine.
- Nothing here lets Jarvis weaken these guardrails itself. Changing this file is a PIN action.

## Where each piece lives
- This policy and `policy.json`: `jarvis/guardrails/` in the Flow repo, installed to `C:\Jarvis\guardrails`.
- Claude Code's own prompts: the installer adds allow rules for the training folder and the intake script to
  `%USERPROFILE%\.claude\settings.json`, so Claude sessions on homebase stop asking about them.
- The homebase approval hook and the Worker (`agent.py`) are maintained by the Worker-queue work; they load
  `policy.json` for the tier of each action.
