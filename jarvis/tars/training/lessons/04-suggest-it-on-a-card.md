# Lesson 4: suggest one of these jobs on a card

Jarvis and TARS may notice the need for Lessons 1-3 and suggest them. They never approve their own suggestions.

**How.**
- File a card on the task board (GitHub flanneryjake/jarvis-tasks). TARS can do it by voice: "tell Claude to ..."
  files a card.
- Title it plainly: "Proposal: rebuild TARS with the new persona rows", "Proposal: house-rules card for <topic>",
  "Proposal: training rows for <mistake>".
- In the body: what you noticed (one or two real examples), which lesson it is, the steps, how to check, how to undo,
  and whether Jake has to type a line.
- Leave it unapproved. Claude reviews it; Jake approves in the app. Training data alone can go ahead (Lesson 1);
  rebuilds and house-rules changes wait for Jake's line.

**Good example.** "Proposal: training rows for over-using 'sir'. Tonight TARS said 'sir' in 9 of 10 replies (log
lines attached). Lesson 1: add 10 rows with 'sir' in 3 of them, run check_training.py, add through training_intake.
Then Lesson 2 rebuild, which needs Jake's line."

**Don't.** File the same proposal twice, or file one with no real example behind it.
