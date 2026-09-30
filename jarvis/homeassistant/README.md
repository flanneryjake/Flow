# Jarvis in Home Assistant

Config for the three approved Home Assistant cards: the HA + Mosquitto server on homebase, an agent on every
machine, and the Jarvis control panel. HA runs as a container in WSL2 Docker on homebase
(`http://100.90.201.22:8123`, Tailscale only), next to Mosquitto on `:1883`.

| File | Goes to | What it does |
| --- | --- | --- |
| `mosquitto/mosquitto.conf` | Mosquitto container's `/mosquitto/config/` | Broker with logins required, no anonymous clients |
| `packages/jarvis.yaml` | `<HA config>/packages/` | Approvals-waiting count and both Machine Health rows from Notion, Wake-rig button, Pi status + last voice command over MQTT, phone alert when a machine row turns red |
| `dashboards/jarvis.yaml` | `<HA config>/dashboards/` | The Jarvis panel: Home summary, Machines, Approvals (Hub v3 embedded), Voice, Logs |
| `linux-agent/` | the Pi (after it's flashed) | Reports CPU/memory/temp/uptime to HA and adds Reboot and Restart-voice buttons |

## Deploy on homebase (the Worker or a Remote Control session does this)

1. Check HA is up: `curl http://127.0.0.1:8123/manifest.json`. If not, read the `Jarvis HA Stack` task's log and fix
   that first; the watchdog now shows HA and MQTT in the Ports line and tails the HA log when HA is down.
2. Mosquitto: copy `mosquitto.conf` over the container's config, create the login once
   (`docker exec -it <mosquitto> mosquitto_passwd -c /mosquitto/config/passwd jarvis`), restart the container.
3. In HA: Settings > Devices > Add integration > MQTT, broker `127.0.0.1` (or the mosquitto container name),
   user `jarvis`.
4. Copy `packages/jarvis.yaml` and `dashboards/jarvis.yaml` into the HA config folder, add to `configuration.yaml`:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   lovelace:
     dashboards:
       jarvis-panel:
         mode: yaml
         title: Jarvis
         icon: mdi:robot
         show_in_sidebar: true
         filename: dashboards/jarvis.yaml
   ```

   and to `secrets.yaml`: `notion_auth: "Bearer <the NOTION_TOKEN the watchdog uses>"`.
5. Developer tools > YAML > Check configuration, then restart HA. `sensor.homebase_worker` should read the same as the
   homebase row in Notion's 🩺 Machine Health.

Approvals count = Tasks cards with Type **Approval** and Status **Inbox**. Approving and snoozing stay in Hub v3
(embedded on the Approvals view) so the PIN rules live in one place.

## Agent on every machine

**Windows (homebase, rig, RTX 5060 laptop): HASS.Agent.** Install from github.com/LAB02-Research/HASS.Agent, point
it at MQTT `100.90.201.22:1883` user `jarvis`, and set the device name to `homebase`, `rig` or `laptop5060`. Add these
so the panel's entity names match (`sensor.<device>_<name>`, `button.<device>_<name>`):

- Sensors: `cpuload` (CpuLoad), `memoryusage` (MemoryUsage), `lastactive` (LastActive); on the rig also `gpuload`
  and `gputemperature`.
- Commands: `restart`, `shutdown`, `sleep`, `lock` (the built-in ones). Power buttons ask for a confirm tap.

**Linux (the Pi): `linux-agent/`.** Setup steps are at the top of `jarvis-agent.service`. Test: `python3 -m unittest
test_jarvis_agent` in that folder.

Restart rules from the 9/25 decision still apply: Claude may restart services and reboot, but shutting a machine down
fully is allowed only once remote wake is verified for it.
