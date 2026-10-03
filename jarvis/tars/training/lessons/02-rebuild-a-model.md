# Lesson 2: rebuild a model (TARS on the 5060, Jarvis on the rig)

**What it is.** TARS and Jarvis are Ollama models: a base model plus a Modelfile (system prompt, example messages,
settings). "Rebuilding" makes a new copy of the model from an updated Modelfile. It does not retrain the weights; that
is a fine-tune, which is a much bigger job on the rig's GPU sandbox.

**When to suggest it.** The persona, house rules or example replies changed in Git, or replies keep drifting from the
rules in a way the Modelfile can fix.

**Steps.**
1. See what is live: `ollama show tars --modelfile` (or `jarvis` on the rig). Note the FROM line and the parameters.
2. Back it up first: `ollama cp tars tars-pre-<what>-<yyyymmdd>`.
3. Get the new Modelfile from Git (TARS: jarvis/tars/Modelfile.ironman). Keep the live FROM line and parameters if
   they differ from the file.
4. Build: `ollama create tars -f Modelfile.ironman`. Do not restart Ollama itself (on the rig, never).
5. Check: ask three short questions (TARS: POST http://127.0.0.1:8790/chat). The answers should follow the rules,
   and "sir" should show up in some replies, not all of them.

**Undo.** `ollama cp tars-pre-<what>-<yyyymmdd> tars`.

**Needs Jake?** Yes, a live change on a PC needs Jake's one typed line naming it, e.g. "I approve rebuilding TARS on
the 5060 with the new persona". So suggest it on a card (Lesson 4); don't do it on your own.
