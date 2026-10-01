""""Hey Jarvis" on the rig's microphone (stopgap until the room speaker / 5060 voice stack exists).

Mic closed unless Home Assistant's "Jarvis listening" switch is on. HA pushes the switch to this listener
(POST http://<rig>:8796/listening {"on": true, "say_url": ...}); it starts OFF at boot and HA re-sends it every
2 minutes, along with the private webhook URL replies go to (so no secret is copied to the rig by hand).
When on: openWakeWord hears "Hey Jarvis" -> records until you stop talking -> faster-whisper (GPU if it can,
else CPU) -> TARS on the laptop answers (it keeps the conversation) -> HA speaks it on the Echo in this room.
Idea mode: after each reply Jarvis keeps listening ~20 s for a follow-up, no wake word needed.
No audio is saved or sent anywhere; only the transcribed words go to TARS.

  pythonw hey_jarvis.py            # normal run (scheduled task "Jarvis Hey Jarvis")
  python hey_jarvis.py --say "hi"  # say a line on the Echo and exit (speaker check)
  python hey_jarvis.py --once      # skip the switch, listen for one request, print it, exit (mic check)
"""
import argparse
import http.server
import json
import os
import queue
import threading
import time
import urllib.request

TARS_URL = os.environ.get("TARS_URL", "http://100.85.255.99:8790/chat")
PORT = int(os.environ.get("HEY_JARVIS_PORT", "8796"))
HOME = os.environ.get("HEY_JARVIS_HOME", r"C:\Jarvis\rig-voice")
# HA webhook that speaks on an Echo (packages/rig_voice.yaml); JARVIS_ECHO picks the Echo in the rig's room.
ECHO = os.environ.get("JARVIS_ECHO", "media_player.kitchen")
HA_WEBHOOKS = os.environ.get("JARVIS_HA_WEBHOOKS", "http://100.90.201.22:8123/api/webhook/")  # only HA may set say_url
FOLLOW_UP_S = 20
MIC = os.environ.get("JARVIS_MIC", "fifine")   # part of the input device's name; the rig's Fifine USB mic
LOG = os.path.join(HOME, "hey_jarvis.log")
RATE = 16000
FRAME = 1280                 # 80 ms, what openWakeWord expects
WAKE_THRESHOLD = 0.5
SILENCE_RMS = 400            # int16 RMS below this counts as quiet
END_SILENCE_S = 1.2
MAX_REQUEST_S = 15

state = {"listening": False, "since": time.time(), "say_url": os.environ.get("JARVIS_SAY_URL", "")}


def log(msg):
    os.makedirs(HOME, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


# ---------------------------------------------------------------- control endpoint (HA pushes the switch)

class Control(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip("/") in ("/listening", "/health"):
            return self._send(200, {"listening": state["listening"]})
        self._send(404, {"error": "try GET/POST /listening"})

    def do_POST(self):
        if self.path.rstrip("/") != "/listening":
            return self._send(404, {"error": "try POST /listening"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            on = body.get("on")
            if isinstance(on, str):
                on = on.lower() in ("on", "true", "1")
            if isinstance(body.get("say_url"), str) and body["say_url"].startswith(HA_WEBHOOKS):
                state["say_url"] = body["say_url"]
            set_listening(bool(on))
            self._send(200, {"listening": state["listening"]})
        except Exception as e:  # noqa: BLE001
            self._send(400, {"error": str(e)})


def set_listening(on):
    if on != state["listening"]:
        state["listening"], state["since"] = on, time.time()
        log("listening " + ("ON" if on else "OFF"))


def serve(port=PORT):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Control)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- speech pieces

def ask_tars(text, url=TARS_URL, timeout=90):
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.loads(r.read()).get("reply") or "").strip()


def rms(chunk):
    import numpy as np
    return float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) if len(chunk) else 0.0


def pick_mic(devices, want=MIC):
    """Index and native rate of the first input device whose name contains `want`, else the default input."""
    for i, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0 and want and want.lower() in d["name"].lower():
            return i, int(d["default_samplerate"])
    return None, None


def to_16k(chunk, rate):
    """Resample one int16 block to 16 kHz (the mic may only do 44.1/48 kHz)."""
    import numpy as np
    if rate == RATE:
        return chunk
    n = int(round(len(chunk) * RATE / rate))
    return np.interp(np.linspace(0, len(chunk) - 1, n), np.arange(len(chunk)), chunk).astype(np.int16)


def load_whisper():
    from faster_whisper import WhisperModel
    try:
        return WhisperModel("small.en", device="cuda", compute_type="float16")
    except Exception as e:  # noqa: BLE001  (no CUDA libs -> CPU)
        log(f"whisper on CPU ({e.__class__.__name__})")
        return WhisperModel("base.en", device="cpu", compute_type="int8")


def speak(text, url=None, echo=ECHO):
    """Hand the reply to HA, which says it on the Echo. Returns roughly how long the Echo will be talking."""
    url = url or state["say_url"]
    if not url:
        raise RuntimeError("no say_url from HA yet")
    req = urllib.request.Request(url, data=json.dumps({"message": text, "echo": echo}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).close()
    return 2.0 + len(text.split()) / 2.6      # ~2.6 words/s plus Alexa's start-up


def run(once=False):
    import numpy as np
    import sounddevice as sd
    from openwakeword.model import Model
    import openwakeword.utils
    openwakeword.utils.download_models(["hey_jarvis"])
    wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    whisper = load_whisper()
    frames = queue.Queue()
    follow_up_until = 0.0
    device, rate = pick_mic(sd.query_devices())
    if device is None:
        rate = int(sd.query_devices(kind="input")["default_samplerate"])
        log(f"no input named {MIC!r}; using the default input")
    log(f"ready on {sd.query_devices(device, 'input')['name']} @ {rate} Hz")
    pending = []

    def on_audio(d, *_):
        # Collect the mic's native blocks, hand openWakeWord exact 80 ms frames at 16 kHz.
        pending.extend(to_16k(d[:, 0].copy(), rate).tolist())
        while len(pending) >= FRAME:
            frames.put(np.array(pending[:FRAME], dtype=np.int16))
            del pending[:FRAME]

    while True:
        if not (once or state["listening"]):
            follow_up_until = 0.0
            time.sleep(1)           # mic stays closed while the switch is off
            continue
        with sd.InputStream(device=device, samplerate=rate, channels=1, dtype="int16",
                            blocksize=int(FRAME * rate / RATE), callback=on_audio):
            got = []
            # Wait for "Hey Jarvis", or, inside the idea-mode window, for any speech at all.
            while (once or state["listening"]) and not got:
                chunk = frames.get()
                in_window = time.time() < follow_up_until
                if in_window and rms(chunk) >= SILENCE_RMS:
                    got = [chunk]
                elif max(wake.predict(chunk).values()) >= WAKE_THRESHOLD:
                    got = [np.zeros(0, dtype=np.int16)]
                    wake.reset()
                elif not in_window and follow_up_until:
                    follow_up_until = 0.0
                    log("idea mode ended")
            if not got:
                continue
            quiet, start = 0.0, time.time()
            while time.time() - start < MAX_REQUEST_S:
                chunk = frames.get()
                got.append(chunk)
                quiet = quiet + FRAME / RATE if rms(chunk) < SILENCE_RMS else 0.0
                if quiet >= END_SILENCE_S and time.time() - start > 1.5:
                    break
        audio = np.concatenate(got).astype(np.float32) / 32768.0
        text = " ".join(s.text for s in whisper.transcribe(audio, language="en")[0]).strip()
        del audio, got               # nothing kept
        if once:
            print(text)
            return
        if len(text) < 3:
            continue
        log(f"heard {len(text.split())} words")
        try:
            reply = ask_tars(text)
        except Exception as e:  # noqa: BLE001
            log(f"TARS failed: {e}")
            reply = "Sorry Jake, TARS on the laptop isn't answering right now."
        try:
            talking = speak(reply[:400])
        except Exception as e:  # noqa: BLE001
            log(f"Echo failed: {e}")
            talking = 0.0
        time.sleep(talking)          # don't take the Echo's own voice as your follow-up
        with frames.mutex:
            frames.queue.clear()
        follow_up_until = time.time() + FOLLOW_UP_S


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--say")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    if a.say:
        return speak(a.say)
    if a.once:
        return run(once=True)
    serve()
    while True:                      # a mic or model error must never take the switch endpoint down with it
        try:
            run()
        except Exception as e:  # noqa: BLE001
            log(f"listener crashed: {e.__class__.__name__}: {e}; retrying in 15 s")
            time.sleep(15)


if __name__ == "__main__":
    main()
