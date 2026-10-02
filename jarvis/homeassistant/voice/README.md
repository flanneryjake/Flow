# Jarvis's own wake word and voice

Echos can't be renamed to "Jarvis": Amazon only allows Alexa, Amazon, Echo, Computer or Ziggy, and Alexa Media
Player can't change the Echo's voice (SSML voice tags are ignored). So Jarvis gets its own voice path through Home
Assistant Assist, all local, and the Echos keep doing Alexa things.

| Piece | Where | What |
| --- | --- | --- |
| Wake word "Hey Jarvis" | `openwakeword` container, or on-device on an HA Voice satellite | built-in model |
| Ears | `whisper` container (`base-int8`, CPU) | speech to text |
| Voice | `piper` container, `en_GB-alan-medium` | British male, local |
| Brain | HA **Ollama** integration -> the laptop's Ollama (`baby-jarvis`), with "Control Home Assistant" on | answers and runs HA actions |
| Pipeline | HA Assist pipeline **Jarvis** (preferred) | ties the four together |
| Mic + speaker | phone (HA Companion app, push-to-talk) today; an always-listening satellite next | where you talk |

## Setup (Remote Control on homebase + laptop)

1. Homebase: copy this folder to `C:\Jarvis\voice`, then `wsl -e bash -c "cd /mnt/c/Jarvis/voice && docker compose up -d"`.
2. HA: add Wyoming integrations for `127.0.0.1` (or the WSL host IP if HA isn't on host networking) ports 10300,
   10200, 10400.
3. Laptop: publish Ollama on the tailnet without opening the firewall:
   `tailscale serve --bg --tcp 11434 tcp://127.0.0.1:11434`.
4. HA: add the Ollama integration, URL `http://100.85.255.99:11434`, model `baby-jarvis`, Control Home Assistant on,
   name it Jarvis. Prompt: the TARS persona from `jarvis/tars/Modelfile`.
5. HA: Settings > Voice assistants > add pipeline **Jarvis**: conversation agent Jarvis, STT whisper, TTS piper
   (`en_GB-alan-medium`), wake word openwakeword `hey_jarvis`. Make it preferred.
6. Expose to Assist only what Jarvis may touch (lights, media, the Jarvis scripts). Locks, alarms and anything that
   spends money stay unexposed, per the PIN rules.

Test: HA Companion app on the phone > Assist (pick Jarvis) > "what time is it" answers in the Jarvis voice.
