---
name: jarvis-morning-report
description: Build or check Jake's 7 AM Jarvis report email (4 pages: done, to do, numbered ideas, passive-income findings) and turn his numbered email replies into task cards.
---

# Jarvis 7 AM report

## Recipients and timing
- Sent at 7 AM ET daily to flanneryjake240@gmail.com, PLUS jpflannery@recoverysolutions.us on Tue-Sat only (his work days).
- One PDF/email. Source of truth: the jarvis-tasks board (GitHub), the jarvis-outputs repo, and the Health issues. Never Notion.

## Layout (4 pages)
1. **Work completed**: each finished item with the model that drafted it (Jarvis on the rig, Tars on the 5060, Gemini, or Claude), where the output is (link), plus the status of every instruction Jake emailed back the day before, and "Training data added".
2. **Work still to do**: what's queued or running, by machine and priority, and what is blocked on what. Before listing anything as "waiting on Jake", check it is still true (stale "waiting on Jake" items have burned him before).
3. **Ideas**, numbered by type: **E#** efficiency, **Q#** product quality, **F#** Fieldwork Clinical, **I#** income.
4. **Passive-income findings**, numbered **P#**, ranked from the newest jarvis-outputs research/passive-income-new-ideas-*.md.
Every idea and finding is tagged **CLAUDE CAN START** (green highlight), **NEEDS JAKE FIRST**, or **STARTED**.

Style: short lines, answer first, ADHD-friendly (most important at the top of each page), links instead of long text.

## Jake's replies
Jake replies from his work email with numbered instructions, e.g. "start P1, P3" or "E2 no". For each:
- Turn it into a jarvis-tasks card: label `from-email`, `status:approved` (unless it's a PIN kind), the right machine and priority, body quoting his instruction and the idea's text.
- Mark the Gmail message with the label "Jarvis/Reply handled" so it's never processed twice.
- Report the new card numbers on the next morning's page 1.

## Overnight routing (why the report looks this way)
When Jake is hands-free the rig's local model drafts, Gemini researches and proofreads non-private text, and Claude only reviews (short passes). Clinical content never goes to Gemini.
