---
name: snooze-until-file
description: Use when an approved Jarvis card can't start because a file it needs doesn't exist yet. Snooze the card until the file appears instead of asking Jake.
---

# Snooze until a file exists

Jake already approved this card. A missing input file is a wait, not a question for him, so never stop the
card as "needs Jake" just because an input file isn't there yet. Snooze it on that file instead. The Worker
checks every poll and puts the card back to approved the moment the file appears.

## When to use it

- The card needs a file that another card, a print, a sync or a download will produce, and it isn't there yet.
- Not for a missing decision, PIN, password, purchase or anything only Jake can give. Those still go to Jake.
- Not when the file can't ever appear (wrong name, nothing produces it). Look first (see step 1).

## Steps

1. **Look for it first.** Search the expected folder, the card's parent ("Spawned from #N"), the parent's run
   comments, and `C:\Jarvis\outputs-repo`. If the file is already somewhere, use it and carry on. Don't snooze.
2. **Work out the full path** where the file will land, e.g. `C:\Jarvis\outputs-repo\cards\card-412\plate5.3mf`.
   - If the card or its parent names the full path, use that.
   - If you only know a bare file name (`plate5.3mf`), give just the name. The Worker then edits the parent
     card (the one that makes the file) so it writes to
     `C:\Jarvis\outputs-repo\cards\card-<parent>\<name>`, and snoozes on that path. If the parent already
     finished, it is reopened only to put its file at that path (copy it there if it exists elsewhere).
3. **Save anything you already did** in the card's work folder, so the next run continues from there.
4. **End the run with this as your very last line**, and stop:

   ```
   SNOOZE_UNTIL: <full path or bare file name> | <one short sentence: what makes it and why you need it>
   ```

   Optional, when you know which card makes the file and it isn't the parent: add ` | producer=#<number>`.

## Outside a Worker run

From a Claude Code session with the tasks token (homebase, the rig, a cloud session):

```
python C:\Jarvis\ghq\ghq.py snooze 412 "C:\Jarvis\outputs-repo\cards\card-410\plate5.3mf" --machine homebase --reason "needs the slice from #410"
python C:\Jarvis\ghq\ghq.py snooze 412 plate5.3mf --machine homebase          # bare name: edits the parent card
python C:\Jarvis\ghq\ghq.py snoozed                                          # what's waiting on what
python C:\Jarvis\ghq\ghq.py wake-snoozed --machine homebase                  # check now instead of next poll
```

`--machine` is the PC where the file will appear (the one whose Worker checks the path).
