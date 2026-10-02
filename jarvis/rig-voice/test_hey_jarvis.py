import http.server
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request

os.environ["HEY_JARVIS_HOME"] = tempfile.mkdtemp()
import hey_jarvis as hj  # noqa: E402


class FakeTars(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        raw = json.dumps({"reply": "You said " + body["text"]}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(raw)


class FakeHA(http.server.BaseHTTPRequestHandler):
    got = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        FakeHA.got.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(200)
        self.end_headers()


class Test(unittest.TestCase):
    def test_speak_posts_to_ha_webhook(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), FakeHA)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        secs = hj.speak("one two three", url="http://127.0.0.1:%d/api/webhook/jarvis_rig_say" % srv.server_port,
                        echo="media_player.kitchen")
        self.assertEqual(FakeHA.got[0], ("/api/webhook/jarvis_rig_say",
                                         {"message": "one two three", "echo": "media_player.kitchen"}))
        self.assertGreater(secs, 2)
        srv.shutdown()

    def test_switch_starts_off_and_follows_ha(self):
        srv = hj.serve(port=0)
        url = "http://127.0.0.1:%d/listening" % srv.server_port
        self.assertFalse(json.loads(urllib.request.urlopen(url).read())["listening"])
        req = urllib.request.Request(url, data=json.dumps({"on": False, "say_url": hj.HA_WEBHOOKS + "x"}).encode(),
                                     method="POST")
        urllib.request.urlopen(req).close()
        self.assertEqual(hj.state["say_url"], hj.HA_WEBHOOKS + "x")
        req = urllib.request.Request(url, data=json.dumps({"on": False, "say_url": "http://evil/x"}).encode(),
                                     method="POST")
        urllib.request.urlopen(req).close()
        self.assertEqual(hj.state["say_url"], hj.HA_WEBHOOKS + "x")
        for sent, want in ((True, True), ("off", False), ("on", True), (False, False)):
            req = urllib.request.Request(url, data=json.dumps({"on": sent}).encode(), method="POST")
            self.assertEqual(json.loads(urllib.request.urlopen(req).read())["listening"], want)
        srv.shutdown()

    def test_pick_mic_by_name(self):
        devs = [{"name": "Speakers (Realtek)", "max_input_channels": 0, "default_samplerate": 48000},
                {"name": "Microphone (FIFINE K669 Microphone)", "max_input_channels": 1, "default_samplerate": 48000}]
        self.assertEqual(hj.pick_mic(devs, "fifine"), (1, 48000))
        self.assertEqual(hj.pick_mic(devs, "blue yeti"), (None, None))

    def test_to_16k(self):
        import numpy as np
        self.assertEqual(len(hj.to_16k(np.zeros(3840, dtype=np.int16), 48000)), 1280)

    def test_ask_tars(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), FakeTars)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.assertEqual(hj.ask_tars("hi", url="http://127.0.0.1:%d/chat" % srv.server_port), "You said hi")
        srv.shutdown()


class TranscribeTest(unittest.TestCase):
    def test_gpu_failure_switches_to_cpu(self):
        class Seg:
            text = " hello there"

        class Gpu:
            def transcribe(self, audio, language):
                raise RuntimeError("Library cublas64_12.dll is not found")

        class Cpu:
            def transcribe(self, audio, language):
                return [Seg()], None

        orig = hj.load_whisper
        hj.load_whisper = lambda cpu=False: Cpu()
        try:
            model, text = hj.transcribe(Gpu(), None)
        finally:
            hj.load_whisper = orig
        self.assertIsInstance(model, Cpu)
        self.assertEqual(text, "hello there")


class HoldingLineTest(unittest.TestCase):
    def test_slow_answer_gets_holding_line(self):
        said = []
        reply = hj.ask_with_holding_line("weather", wait=0.05, ask=lambda t: (time.sleep(0.2), "Rain later, sir.")[1],
                                         say=said.append)
        self.assertEqual((said, reply), (["One moment, sir."], "Rain later, sir."))

    def test_fast_answer_has_no_holding_line(self):
        said = []
        reply = hj.ask_with_holding_line("hi", wait=1, ask=lambda t: "Hello, sir.", say=said.append)
        self.assertEqual((said, reply), ([], "Hello, sir."))

    def test_tars_down(self):
        def boom(t):
            raise OSError("down")
        self.assertIn("isn't answering", hj.ask_with_holding_line("hi", wait=1, ask=boom, say=lambda m: None))


if __name__ == "__main__":
    unittest.main()
