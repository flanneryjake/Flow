---
name: snooze-until-file
description: Use when an approved Jarvis card can't go on because it is waiting on something other than Jake (a file, another card, a PC, a time). Snooze it on that instead of sending it back for approval.
---

# Snooze instead of sending it back to Jake

Jake already approved this card. Sending it back to him for something he can't fix makes him approve the same
card over and over. So when the card is blocked, first work out **what it is actually waiting on**:

| Waiting on | Snooze line |
|---|---|
| A file another card, a print, a sync or a download will make | `SNOOZE_UNTIL: C:\full\path\file.ext \| reason` (or just `file.ext`, see below) |
| Another card finishing | `SNOOZE_UNTIL: card:412 \| reason` |
| A PC being online | `SNOOZE_UNTIL: machine:rig \| reason` (homebase, rig, laptop, pi) |
| A time (usage reset, store opens, after a print) | `SNOOZE_UNTIL: time:2026-10-03T09:00:00Z \| reason` |

Only stop for Jake when nothing but Jake can unblock it: a decision or preference only he can make, a PIN,
spending money, posting or sending something outside, deleting something, a password or login, or something
physical (plug in, load filament, press a button). Those still end the run as "needs Jake" as before.

## Steps

1. **Look first.** If the file is already somewhere (the card's folder, the parent card "Spawned from #N" and
   its run comments, `C:\Jarvis\outputs-repo`), or the other card is already done, use it and carry on.
2. **Pick the condition** from the table. For a file, give the full path where it will land. If you only know
   the name (`plate5.3mf`), give just the name: the Worker edits the parent card (the one that makes it) to
   write to `C:\Jarvis\outputs-repo\cards\card-<parent>\<name>` and snoozes on that. If a different card makes
   it, add ` | producer=#<number>`.
3. **Save what you already did** in the card's work folder so the next run continues from there.
4. **End the run with the snooze line as your very last line**, and stop.

The Worker checks the condition every poll and puts the card back to approved when it is met.

## The safety net (no work for you)

Even if a run ends as "needs Jake", the Worker asks a model what the card is really waiting on before it goes
to Jake (the local model first, Claude if that fails). Anything that isn't really Jake gets snoozed instead.
A card snoozed 3 times that is still stuck goes to Jake once, with its history, so it can't loop forever.

## Outside a Worker run

From a Claude Code session with the tasks token (homebase, the rig, a cloud session):

```
python C:\Jarvis\ghq\ghq.py snooze 412 "C:\Jarvis\outputs-repo\cards\card-410\plate5.3mf" --machine homebase --reason "needs the slice from #410"
python C:\Jarvis\ghq\ghq.py snooze 412 plate5.3mf --machine homebase          # bare name: edits the parent card
python C:\Jarvis\ghq\ghq.py snooze 412 --until card:410 --machine homebase    # also machine:rig, time:<ISO>
python C:\Jarvis\ghq\ghq.py send-back 412 "why it stopped" --machine homebase # triage instead of asking Jake
python C:\Jarvis\ghq\ghq.py snoozed                                          # what's waiting on what
python C:\Jarvis\ghq\ghq.py wake-snoozed --machine homebase                  # check now instead of next poll
```

`--machine` is the PC whose Worker checks the condition (for a file, the PC it lands on).
