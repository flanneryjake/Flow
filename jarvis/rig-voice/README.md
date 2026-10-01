# "Hey Jarvis" on the rig's mic (stopgap)

Until the room speaker or the 5060 voice stack exists, the rig's microphone is Jarvis's ear.

- **Switch:** "Jarvis listening" in Home Assistant (and the Jarvis app). Off = mic closed. The Kitchen Echo says when
  it flips. HA pushes the switch to the listener every 2 minutes and on every change.
- **Talk:** say "Hey Jarvis", then talk. After each reply you have ~20 s to keep going without the wake word (idea
  mode). TARS on the laptop answers and keeps the thread of the conversation.
- **Hear:** HA says the reply on the Echo set by `JARVIS_ECHO` (default `media_player.kitchen`).
- **Privacy:** audio never leaves the rig and is never saved; only the words go to TARS. Massachusetts needs everyone's
  consent to record a conversation, so leave the switch off with guests unless they agree.
- **Catch:** only works while the rig is awake. Switched off, the listener never counts as busy; whether "on" should
  keep the rig awake is decided with the rig auto-power thread.

## Install (rig, Remote Control)

```powershell
New-Item -ItemType Directory -Force C:\Jarvis\rig-voice | Out-Null
Copy-Item hey_jarvis.py C:\Jarvis\rig-voice\
python -m pip install openwakeword onnxruntime faster-whisper sounddevice numpy
# GPU speech-to-text (optional, falls back to CPU): python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
python C:\Jarvis\rig-voice\hey_jarvis.py --once     # mic check: say "Hey Jarvis, testing", prints the words
tailscale serve --bg --tcp 8796 tcp://127.0.0.1:8796
```

Then a hidden logon task "Jarvis Hey Jarvis" running `pythonw C:\Jarvis\rig-voice\hey_jarvis.py`, with
`JARVIS_ECHO` set to the Echo in the rig's room. On homebase, add `rig_voice_listening_url` to HA's `secrets.yaml`
(`http://<rig tailnet IP>:8796/listening`) plus a random `rig_voice_webhook_id` and the matching `rig_voice_say_url`
(see the top of `packages/rig_voice.yaml`), and copy that package in. HA hands the reply URL to the rig itself.

Tests: `python -m unittest test_hey_jarvis` (no mic or network needed).
