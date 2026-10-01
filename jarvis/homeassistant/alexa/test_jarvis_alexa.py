import datetime as dt
import http.server
import json
import os
import tempfile
import threading
import unittest

os.environ["JARVIS_ALEXA_HOME"] = tempfile.mkdtemp()
import jarvis_alexa as ja  # noqa: E402

KITCHEN = {"entity_id": "media_player.kitchen_echo", "state": "idle",
           "attributes": {"friendly_name": "Kitchen Echo", "last_called": True,
                          "last_called_timestamp": 1790000000000, "last_called_summary": "turn off the lights"}}
TV = {"entity_id": "media_player.living_room_tv", "state": "off", "attributes": {"friendly_name": "TV"}}


class FakeHA(http.server.BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        raw = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        FakeHA.calls.append(("GET", self.path, self.headers.get("Authorization")))
        self._send([KITCHEN, TV] if self.path == "/api/states" else [[KITCHEN]])

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        FakeHA.calls.append(("POST", self.path, body))
        if self.path == "/auth/login_flow":
            self._send({"flow_id": "f1"})
        elif self.path == "/auth/login_flow/f1":
            self._send({"type": "create_entry", "result": "code1"})
        elif self.path == "/auth/token":
            self._send({"access_token": "acc", "refresh_token": "ref"})
        else:
            self._send([])


class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), FakeHA)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        ja.HA_URL = "http://127.0.0.1:%d" % cls.srv.server_port
        ja.CLIENT_ID = ja.HA_URL + "/"
        ja._save(ja.BOT_FILE, {"username": "jarvis-bot", "password": "pw"})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakeHA.calls.clear()

    def test_echo_players_only_alexa(self):
        self.assertEqual([p["entity_id"] for p in ja.echo_players([KITCHEN, TV])], ["media_player.kitchen_echo"])

    def test_pick_echo_by_friendly_name(self):
        self.assertEqual(ja.pick_echo([KITCHEN], "Kitchen"), "media_player.kitchen_echo")
        with self.assertRaises(SystemExit):
            ja.pick_echo([KITCHEN], "bedroom")

    def test_heard_filters_by_time(self):
        old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        self.assertEqual(ja.heard_from([KITCHEN], old)[0]["said"], "turn off the lights")
        self.assertEqual(ja.heard_from([KITCHEN], dt.datetime.now(dt.timezone.utc)), [])

    def test_slim_drops_noise(self):
        self.assertIsNone(ja.slim({"entity_id": "sun.sun", "state": "above_horizon"}))
        row = ja.slim(KITCHEN)
        self.assertNotIn("friendly_name", row["attrs"])
        self.assertEqual(row["attrs"]["last_called_summary"], "turn off the lights")

    def test_login_then_say_targets_all_echos(self):
        ja.main(["login"])
        self.assertEqual(ja._load(ja.TOKEN_FILE)["refresh_token"], "ref")
        ja.main(["say", "hello", "--announce"])
        notify = [c for c in FakeHA.calls if c[1] == "/api/services/notify/alexa_media"][0]
        self.assertEqual(json.loads(notify[2]), {"message": "hello", "target": ["media_player.kitchen_echo"],
                                                 "data": {"type": "announce"}})
        self.assertIn(("GET", "/api/states", "Bearer acc"), FakeHA.calls)

    def test_do_sends_custom_command(self):
        ja._save(ja.TOKEN_FILE, {"refresh_token": "ref"})
        ja.main(["do", "turn off the lights"])
        call = [c for c in FakeHA.calls if c[1] == "/api/services/media_player/play_media"][0]
        self.assertEqual(json.loads(call[2])["media_content_type"], "custom")

    def test_first_real_echo_skips_groups(self):
        group = {"entity_id": "media_player.everywhere", "attributes": {"friendly_name": "Everywhere"}}
        self.assertEqual(ja.first_real_echo([group, KITCHEN]), "media_player.kitchen_echo")

    def test_export_writes_jsonl(self):
        ja._save(ja.TOKEN_FILE, {"refresh_token": "ref"})
        ja.main(["export", "--day", "2026-10-01"])
        with open(os.path.join(ja.ROUTINE_DIR, "2026-10-01.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual(rows[0]["entity"], "media_player.kitchen_echo")


if __name__ == "__main__":
    unittest.main()
