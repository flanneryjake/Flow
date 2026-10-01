# Jarvis and the Alexas

Connects the Echo devices to Jarvis through Home Assistant on homebase. Home Assistant is the hub: the
**Alexa Media Player** integration (custom, from github.com/alandtse/alexa_media_player) signs in to Jake's Amazon
account and turns every Echo into a `media_player` in HA. Jarvis then talks to HA, never to Amazon directly.

What Jarvis gets:

- **Speak on any Echo**: `python jarvis_alexa.py say "Rig is done printing" --echo bedroom` (or the `script.jarvis_say`
  script inside HA, for automations).
- **What was said to Alexa**: each Echo reports its last request (`last_called_summary`); the
  `sensor.alexa_last_heard` template sensor tracks the newest one across the apartment.
- **Routine log**: HA keeps 120 days of history, and `Jarvis Routine Export` writes yesterday's state changes (Echo
  requests, alarms, timers, media, plus any lights, plugs or phones HA knows about) to
  `C:\Jarvis\routine\YYYY-MM-DD.jsonl` every night at 4:05 AM. That is the raw material for learning the routine.

## Install (homebase, Remote Control session)

Prerequisite: HA onboarding finished (Jake's owner account exists). Then, from this folder:

```powershell
powershell -ExecutionPolicy Bypass -File install-alexa.ps1
```

It copies the bridge to `C:\Jarvis\alexa`, and in WSL installs the integration (no HACS, so no GitHub sign-in),
adds `packages/alexa.yaml`, sets `recorder: purge_keep_days: 120`, creates a `jarvis-bot` HA user whose password stays
in `C:\Jarvis\secrets\ha-bot.json`, checks the config and restarts HA. Then it logs `jarvis-bot` in (refresh token in
`C:\Jarvis\secrets\ha-token.json`) and schedules the nightly export. Re-running is safe. A backup of
`configuration.yaml` is saved next to it on every run.

## Jake's part (phone, on the tailnet, about 5 minutes)

1. Open http://100.90.201.22:8123 and create the owner account (skip if done).
2. In HA: Settings > Devices & services > Add integration > **Alexa Media Player**. Amazon region `amazon.com`, your
   Amazon email and password, and set the HA URL to `http://100.90.201.22:8123`. Submit, sign in to Amazon on the
   page it opens (approve the 2-step code), and you're done. The Echos show up as media players.

## Check it works

```powershell
python C:\Jarvis\alexa\jarvis_alexa.py devices
python C:\Jarvis\alexa\jarvis_alexa.py say "Jarvis is connected" --announce
python C:\Jarvis\alexa\jarvis_alexa.py heard --hours 24
```

Tests: `python -m unittest test_jarvis_alexa` in this folder (fake HA server, no network).

## Later

- **"Alexa, tell Jarvis ..."**: Alexa answers "I don't know that" to unknown phrases, so voice commands for Jarvis need
  an Alexa app routine per phrase (action: Wait). HA still sees the words through `sensor.alexa_last_heard`, and an
  automation can turn them into task cards.
- **Alexa controlling HA things by voice** (rig wake, Jarvis scripts): needs the Alexa Smart Home skill (Nabu Casa,
  or a self-hosted AWS Lambda). Not needed for Jarvis to use the Echos.
- Smart plugs and bulbs already paired to Alexa should be added to HA with their own integration where one exists, so
  they land in the routine log.
