# Lesson 3: adjust the rig's house rules (Jarvis)

**What it is.** Jake's standing rules live as short cards in jarvis/knowledge/cards/*.md. Each card has `triggers`
(words that bring it up) and a one-line `summary`. TARS pulls in the matching cards for each question. Jarvis on the rig
gets every summary baked into its system prompt, between `### JARVIS HOUSE RULES START` and `### JARVIS HOUSE RULES END`.

**When to suggest it.** Jake states a new standing rule ("from now on...", "never...", "always..."), or a rule changed
and a card still says the old thing.

**Steps.**
1. Add or edit the card in Git (jarvis/knowledge/cards/<topic>.md): front matter with name, triggers, summary, then a
   few short bullet lines. No secrets, no dollar amounts, no account numbers.
2. On the rig, copy the cards into its knowledge folder, then build a new Modelfile from the live model:
   `python knowledge.py modelfile --base jarvis --out Modelfile.jarvis-skills`
   (it keeps Jarvis's current system prompt and swaps in the new house-rules block, so running it twice is safe).
3. Back up and build: `ollama cp jarvis jarvis-pre-<what>-<yyyymmdd>`, then `ollama create jarvis -f Modelfile.jarvis-skills`.
   Never restart Ollama or agent.py on the rig.
4. Check: `ollama show jarvis --system` contains the new summary line; ask Jarvis one question that should use it.

**Undo.** `ollama cp jarvis-pre-<what>-<yyyymmdd> jarvis`.

**Needs Jake?** Yes for the live rebuild (his typed line naming it). Editing the card in Git doesn't.
