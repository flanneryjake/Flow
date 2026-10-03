---
name: jarvis-risk-and-sandbox
description: Decide whether a Jarvis change can just run, needs a Docker sandbox test on the rig first, or needs Jake's approval or PIN; Jake's guardrail tiers and hard stops.
---

# Risk tiers and the Docker sandbox

Jake wants to be hands-free and hates repeat approval prompts ("ask once, not over and over"), but he does not want to approve risky code blind.

## Tiers (Jarvis guardrails, policy.json in C:\Jarvis\guardrails)
1. **Free**: edits in the work folders, running local models, Gemini on non-private text, task cards, restarting Jarvis services (not the backup's hub/Worker), Workers running their own scripts in their own work folder.
2. **Free + logged**: adding training data (via training_intake.py, logged), rebuilding a local model, pushing claude/* branches, deleting your own files with a backup.
3. **Ask once per kind**: new scheduled tasks, installs, system settings, edits outside work dirs, anything unlisted. Ask Jake ONCE for the kind, then remember it.
4. **PIN every time**: money/spending, posting or sending outside (email, social, store), deleting other people's files or bulk deletes, accounts and credentials, exposing a service to the internet, merging to main, editing the guardrails.

## Docker sandbox rule (Jake, 2026-10-02)
"Anything that requires my approval because of code, run it and troubleshoot it in the Docker first and make sure it works as intended."
- Where: Docker Desktop on the rig (WSL2), C:\Jarvis\sandbox\sandbox.ps1. It only runs while the rig is awake. Never Docker on the backup; none on the 5060 (no RAM).
- Low risk (docs, text, read-only checks): skip the sandbox.
- **Medium risk**: run it in the sandbox, fix until it passes (up to 3 tries). A clean PASS that does what was intended needs NO PIN or approval: proceed and retitle the card "[Docker-tested] <plain-English intent> - <how it went>".
- **High risk**: sandbox first, then still ask Jake, with "Tested in Docker sandbox: PASS" and sandbox-result.md attached.
- FAIL after 3 tries -> Jake. Windows-only parts (registry, scheduled tasks, .bat/.cmd, services) can't really run in Linux Docker: give them a static check or dry run and SAY they weren't executed.
- PIN kinds always go to Jake, sandbox or not.

## Hard stops (no exceptions)
- Clinical, legal, or Fieldwork Clinical customer-facing content never goes to Gemini (free tier may train on it, and it misses domain errors). Claude or Jake reviews it.
- No PHI, patient identifiers, or secrets in training data, repos, memory, or skills.
- No raw secrets in chat. Jake saves secrets as a .txt on the rig desktop (no "key"/"token" in the name).
- Every clinical/safety handout includes: "If you are in crisis, call or text 988 (Suicide & Crisis Lifeline, US)".
- Zero interaction up to deploy; at "ready to deploy" post ONE "Project complete, awaiting approval" card with a risk rating.

## Asking Jake
One question, answerable in one word, options on short lines, your recommendation marked. Never ask twice for the same kind.
