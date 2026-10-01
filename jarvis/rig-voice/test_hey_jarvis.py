import http.server
import json
import os
import tempfile
import threading
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

    def test_ask_tars(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), FakeTars)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.assertEqual(hj.ask_tars("hi", url="http://127.0.0.1:%d/chat" % srv.server_port), "You said hi")
        srv.shutdown()


if __name__ == "__main__":
    unittest.main()
