#!/usr/bin/env python3
"""Jarvis Linux agent: reports a Linux box (the Pi) to Home Assistant over MQTT and takes a few commands.

Sensors (via MQTT discovery, so they appear in HA on their own): CPU %, memory %, CPU temperature, uptime.
Buttons: reboot, and restart the voice service. Only those two commands exist; anything else sent to the
command topic is logged and ignored.

Config comes from the environment (see jarvis-agent.service):
  MQTT_HOST, MQTT_PORT (1883), MQTT_USER, MQTT_PASS
  JARVIS_NODE        entity prefix, default "jarvis_pi"  -> sensor.jarvis_pi_cpu, button.jarvis_pi_reboot
  JARVIS_BASE_TOPIC  default "jarvis/pi"; status is <base>/status (online/offline, retained, last will)
  JARVIS_VOICE_UNIT  systemd unit the restart button restarts, default "jarvis-voice.service"
  JARVIS_INTERVAL    seconds between reports, default 30
"""
import json
import logging
import os
import subprocess
import time

NODE = os.environ.get("JARVIS_NODE", "jarvis_pi")
BASE = os.environ.get("JARVIS_BASE_TOPIC", "jarvis/pi")
VOICE_UNIT = os.environ.get("JARVIS_VOICE_UNIT", "jarvis-voice.service")
INTERVAL = int(os.environ.get("JARVIS_INTERVAL", "30"))
STATUS = f"{BASE}/status"
STATE = f"{BASE}/state"
COMMAND = f"{BASE}/cmd"

SENSORS = {
    "cpu": {"name": "CPU", "unit_of_measurement": "%", "state_class": "measurement", "icon": "mdi:cpu-64-bit"},
    "memory": {"name": "Memory", "unit_of_measurement": "%", "state_class": "measurement", "icon": "mdi:memory"},
    "temperature": {"name": "Temperature", "unit_of_measurement": "°C", "device_class": "temperature",
                    "state_class": "measurement"},
    "uptime": {"name": "Uptime", "unit_of_measurement": "h", "icon": "mdi:timer-outline"},
}
COMMANDS = {
    "reboot": ["sudo", "-n", "systemctl", "reboot"],
    "restart_voice": ["sudo", "-n", "systemctl", "restart", VOICE_UNIT],
}
BUTTONS = {"reboot": "Reboot", "restart_voice": "Restart voice service"}

log = logging.getLogger("jarvis-agent")


def device():
    return {"identifiers": [NODE], "name": NODE.replace("_", " ").title(), "manufacturer": "Jarvis"}


def discovery_messages():
    """(topic, payload) pairs that make HA create this machine's sensors and buttons."""
    msgs = []
    for key, extra in SENSORS.items():
        cfg = {"unique_id": f"{NODE}_{key}", "object_id": f"{NODE}_{key}", "state_topic": STATE,
               "value_template": "{{ value_json.%s }}" % key, "availability_topic": STATUS,
               "device": device(), **extra}
        msgs.append((f"homeassistant/sensor/{NODE}/{key}/config", cfg))
    for key, name in BUTTONS.items():
        cfg = {"unique_id": f"{NODE}_{key}", "object_id": f"{NODE}_{key}", "name": name,
               "command_topic": COMMAND, "payload_press": key, "availability_topic": STATUS,
               "device": device()}
        if key == "reboot":
            cfg["device_class"] = "restart"
        msgs.append((f"homeassistant/button/{NODE}/{key}/config", cfg))
    return msgs


_last_cpu = None


def cpu_percent():
    global _last_cpu
    with open("/proc/stat") as f:
        vals = [int(v) for v in f.readline().split()[1:]]
    idle, total = vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)
    prev, _last_cpu = _last_cpu, (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def memory_percent():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
    return round(100.0 * (1 - info["MemAvailable"] / info["MemTotal"]), 1)


def temperature():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except OSError:
        return None


def uptime_hours():
    with open("/proc/uptime") as f:
        return round(float(f.read().split()[0]) / 3600.0, 1)


def read_state():
    return {"cpu": cpu_percent(), "memory": memory_percent(), "temperature": temperature(),
            "uptime": uptime_hours()}


def run_command(payload):
    """Runs an allowlisted command; returns the argv run, or None when the payload isn't one."""
    argv = COMMANDS.get(payload.strip())
    if not argv:
        log.warning("ignored unknown command %r", payload[:80])
        return None
    log.info("running %s", " ".join(argv))
    subprocess.Popen(argv)
    return argv


def main():
    import paho.mqtt.client as mqtt  # imported here so the helpers above can be tested without paho

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{NODE}-agent")
    if os.environ.get("MQTT_USER"):
        client.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASS", ""))
    client.will_set(STATUS, "offline", retain=True)

    def on_connect(c, _userdata, _flags, reason, _props):
        log.info("connected to MQTT (%s)", reason)
        for topic, cfg in discovery_messages():
            c.publish(topic, json.dumps(cfg), retain=True)
        c.publish(STATUS, "online", retain=True)
        c.subscribe(COMMAND)

    client.on_connect = on_connect
    client.on_message = lambda _c, _u, msg: run_command(msg.payload.decode("utf-8", "replace"))
    client.connect(os.environ.get("MQTT_HOST", "100.90.201.22"), int(os.environ.get("MQTT_PORT", "1883")))
    client.loop_start()
    cpu_percent()  # prime the CPU counter
    while True:
        time.sleep(INTERVAL)
        client.publish(STATE, json.dumps(read_state()))


if __name__ == "__main__":
    main()
