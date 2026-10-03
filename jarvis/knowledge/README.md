# Jarvis house rules (skills for the local models)

What the Apartment Jarvis project learned, in two shapes:

| Folder | For | How it's used |
|---|---|---|
| `jarvis/skills/<name>/SKILL.md` | Claude Code on the PCs (Remote Control sessions, the Worker's `claude -p`) | `python knowledge.py install-skills` copies them to `%USERPROFILE%\.claude\skills`. Changed copies are kept as `SKILL.md.bak-<date>`. |
| `jarvis/knowledge/cards/*.md` | Tars (5060) and Jarvis (rig) | Short cards with a `triggers` regex and a one-line `summary`. No secrets, no dollar amounts, no account numbers. |

The same skills (plus two personal ones, budget and job applications, that stay out of this repo) are Jake's claude.ai
skills, so a fresh Claude chat knows them too.

## Tars (5060)
`knowledge.context_for(text)` returns a `HOUSE RULES` block with at most 2 matching cards (about 1.6 KB max), or `''`
for small talk. `tars_server.py` adds it to the `[context]` next to LIVE / FACTS (hook lives with the Tars code on the
Tars branch). Copy this folder to `C:\Jarvis\knowledge` on the 5060.

## Jarvis (rig)
The rig's `jarvis` model gets every card's summary baked into its system prompt, between
`### JARVIS HOUSE RULES START/END` (re-running replaces the block):

```
ollama cp jarvis jarvis-pre-skills-YYYYMMDD
python knowledge.py modelfile --base jarvis-pre-skills-YYYYMMDD --out Modelfile.jarvis-skills
ollama create jarvis -f Modelfile.jarvis-skills
```
Roll back: `ollama cp jarvis-pre-skills-YYYYMMDD jarvis`.

## Adding a card
Front matter `name`, `triggers` (Python regex, case-insensitive), `summary` (one line); body = 3-5 short bullets in plain
spoken English. Keep the Iron Man JARVIS persona rules (no Boston voice). Run `python -m unittest test_knowledge`.
