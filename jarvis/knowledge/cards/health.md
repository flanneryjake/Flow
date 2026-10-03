---
name: health
triggers: \b(is down|went down|offline|stuck|stall\w*|not working|broken|health|remote control|crash\w*|asleep|unreachable)\b
summary: Health: each PC reports to a Health card every 5 minutes; a red rig is usually just asleep; Claude fixes problems itself through Remote Control.
---
- Each PC's watchdog updates its Health card every 5 minutes (homebase #1, rig #2, backup #889). Red means no check-in for 15 minutes.
- A red rig is usually just asleep, which is normal. The 5060 drops off if its screen is forced off, because it is a Modern Standby laptop.
- A stalled Worker is most often a Claude usage limit; it resumes by itself after the reset.
- Claude fixes machine problems itself through Remote Control. Jake only taps Yes on admin prompts.
