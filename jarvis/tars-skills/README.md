# Tars skills (rig assistant items 5-7)

`tars_skills.py` is one stdlib module with the three jobs that make Tars the rig's assistant. Each one
calls Tars on the laptop and then checks its answer in code, so a wrong model answer can't let a bad
draft or an unsafe Home Assistant call through. Built and tested on LAPTOP-4150EGRS against the live model
(`python eval_tars_skills.py`: checker 6/6, router 15/15, Home Assistant 8/8, run twice).

Set `TARS_OLLAMA_URL=http://laptop-4150egrs:11434` on homebase (the default is localhost).

| Item | Function | Returns | Wire it in |
| --- | --- | --- | --- |
| 5 Rig output checker | `check_rig_output(text, title, required_sections, crisis_line=True, min_words, max_words, warn_sections)` | `pass`, `problems`, `warnings`, `words`, `notify` (one hub line) | After each rig curriculum job. Fails: the 988/911 line, required sections, placeholders (`[Insert ...]`, `{{...}}`, TODO, TBD, lorem) and word limits. Warnings only: a `RIG-NOTES` block left in, and `Include:` sections (`include_sections(job_prompt)`) not found as headings, the same as `Check-RigOutput` in worker.ps1. If the laptop is off, the code checks still run and `notify` uses a template. |
| 6 Front door | `route_request(text, context='')` | `route` laptop/rig/claude, `why`, `handoff`, `answer` | Hub requests go here first. laptop: send `answer` back. rig: make a rig job from `handoff`. claude: make a card (approval and PIN rules unchanged). Pass the Machine Health or queue lines as `context` so "is the rig on" is answered from real state. Web, buying, email and posting always go to claude, and long-form, code, clinical and "N words/pages" requests always go to the rig, whatever the model says. If the laptop is off, it returns rig as the default path. |
| 7 Home Assistant | `ha_intent(text, entities=None)` | `ok`, `call` ({service, target, data}), `why` | Lights and climate only (`light.turn_on/off/toggle`, `climate.set_temperature/set_hvac_mode/turn_on/off`). Locks, alarms, doors, garage and covers are refused before the model sees them, and temperatures must be 55-85. Pass HA's real entity and area ids as `entities` so targets are checked against what exists. Only `ok: true` calls go to HA. |

CLI: `python tars_skills.py check draft.md --sections "Objectives,Closing"`, `python tars_skills.py route "..."`,
`python tars_skills.py ha "..." --entities ids.json`.

## Item 8: laptop jobs for review
The laptop's watchdog pushes `JarvisAgent\logs\laptop-jobs.jsonl` (secrets redacted) every hour to the orphan
branch `laptop-review` of `flanneryjake/jarvis-outputs`, as `review/laptop-jobs.jsonl`. Homebase fetches that
branch into `C:\Jarvis\training\tars\review\`. That folder is a review queue, not training rows.
