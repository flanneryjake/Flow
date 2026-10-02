import unittest
from unittest import mock

import jarvis_agent as a


class DiscoveryTest(unittest.TestCase):
    def test_entity_ids_match_the_dashboard(self):
        ids = {cfg["object_id"] for _, cfg in a.discovery_messages()}
        self.assertEqual(ids, {"jarvis_pi_cpu", "jarvis_pi_memory", "jarvis_pi_temperature", "jarvis_pi_uptime",
                               "jarvis_pi_reboot", "jarvis_pi_restart_voice"})

    def test_everything_goes_unavailable_with_the_status_topic(self):
        for _, cfg in a.discovery_messages():
            self.assertEqual(cfg["availability_topic"], "jarvis/pi/status")


class CommandTest(unittest.TestCase):
    def test_only_allowlisted_commands_run(self):
        with mock.patch.object(a.subprocess, "Popen") as popen:
            self.assertIsNone(a.run_command("rm -rf /"))
            self.assertIsNone(a.run_command("reboot; ls"))
            popen.assert_not_called()
            self.assertEqual(a.run_command("restart_voice\n")[-1], "jarvis-voice.service")
            popen.assert_called_once()


class StatsTest(unittest.TestCase):
    def test_reads_proc_on_linux(self):
        a.cpu_percent()
        s = a.read_state()
        self.assertTrue(0 <= s["memory"] <= 100)
        self.assertGreaterEqual(s["uptime"], 0)  # a freshly booted box rounds to 0.0 h


if __name__ == "__main__":
    unittest.main()
