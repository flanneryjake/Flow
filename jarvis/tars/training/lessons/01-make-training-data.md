# Lesson 1: make training data

**What it is.** Examples that teach a model how Jake wants it to talk and act: short conversations, rules, how-tos.

**When to suggest it.** Jake corrects a reply ("don't say that", "too long", "that's Tony Stark, not JARVIS"), the same
mistake shows up twice in the logs, or Jake asks for something new that the model has no examples of.

**Rules (never break these).**
- No secrets, keys, passwords, account numbers, money amounts, or patient or client details. No clinical text to Gemini.
- Never train TARS on TARS's own answers unless a person or Claude reviewed them first.
- Persona: Paul Bettany's JARVIS. Calm, polite, dry British wit. "Sir" once in a while, never every line. Never Tony
  Stark, never the Boston voice, never "kid".

**Steps.**
1. Write the examples in the repo first (GitHub flanneryjake/flow, folder jarvis/tars/training):
   - conversations.jsonl: one JSON line per conversation, each user turn starting with the [context] block the server builds.
   - prompts.jsonl: test prompts with the expected lane.
   - modelfile_examples.txt: the 16 MESSAGE pairs baked into the Modelfile.
2. Run `python check_training.py` in that folder. It must say "OK: all checks passed" (it checks banned words, length,
   routing lines, made-up numbers, and that "sir" is in about 30-40% of replies).
3. On the PC, add the files through the free lane, which logs every batch and can undo it:
   `python C:\Jarvis\guardrails\training_intake.py add <file> --topic <topic> --source "<where it came from>"`
4. Check: `python C:\Jarvis\guardrails\training_intake.py list --hours 1` shows the batch.

**Undo.** `python C:\Jarvis\guardrails\training_intake.py revert <batch-id>`.

**Needs Jake?** No. Adding training data is pre-approved (logged). A fine-tune run that uses it is a separate card.
