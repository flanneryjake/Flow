# Hal9000 (the Jarvis Pi)

A Raspberry Pi booting from a 128 GB M.2 drive in a USB enclosure. It's the always-on, low-power helper next to Home
Assistant on the junk laptop (homebase backup, `http://100.90.201.22:8123`):

- **Tailnet node** `hal9000`, with Tailscale SSH, so every machine and the phone can reach it.
- **Jarvis agent** (`../homeassistant/linux-agent/`): CPU, memory, temperature and uptime show up in Home Assistant
  on their own through MQTT discovery, plus Reboot and Restart-voice buttons.
- **Local model (later)**: if it runs one, it goes in Ollama as `hal9000`, shown as Hal9000.
- **Self-healing**: unattended security updates and the hardware watchdog (a hung Pi reboots itself).
- **Next, when a USB mic and speaker are attached**: the always-listening "Hey Jarvis" satellite for the Jarvis voice
  pipeline (`../homeassistant/voice/`). The HA package already listens on `jarvis/pi/...` for it.

OS: Raspberry Pi OS Lite 64-bit (Trixie, cloud-init first boot). Works on a Pi 4 or Pi 5.

## Flashing (at the rig)

1. Plug the M.2 enclosure into the rig.
2. In a normal PowerShell window paste:

   ```powershell
   $t=[Environment]::GetEnvironmentVariable('GITHUB_TASKS_TOKEN','User'); if(!$t){$t=Read-Host 'GitHub token'; [Environment]::SetEnvironmentVariable('GITHUB_TASKS_TOKEN',$t,'User')}; irm -Headers @{Authorization="Bearer $t"; Accept='application/vnd.github.raw'} 'https://api.github.com/repos/flanneryjake/Flow/contents/jarvis/pi/pi-flash-kit.ps1?ref=claude/eager-knuth-lakcxt' | iex
   ```

   It installs Imager, downloads and verifies the image, and asks only for what it doesn't have yet (Wi-Fi is read
   from the rig's saved Wi-Fi when it can; Tailscale key; MQTT password, optional).
3. Imager opens. Device: your Pi. OS: **Use custom** > the `.img.xz` in `C:\Jarvis\pi`. Storage: the 128 GB drive.
   Customisation: **No**. Write.
4. When Imager finishes, unplug the drive and plug it back in (Cancel any "format this disk" popup). The script copies
   the first-boot files and ejects the drive.
5. On the Pi: drive into a **blue USB 3 port** (the Pi's USB-C port is power only), no SD card, Ethernet if handy,
   then power. First boot takes about 10 minutes.

Done when `ssh hal9000` works from the rig, or `Hal9000` appears in Home Assistant. The first-boot report is
`/boot/firmware/jarvis/FIRSTBOOT-RESULT.txt` (also readable by plugging the drive into the rig) and the full log is
`/var/log/jarvis-firstboot.log`.

## Notes

- Secrets live only on the rig in `%USERPROFILE%\.jarvis\pi-secrets.json` (Wi-Fi, Tailscale key, MQTT password and the
  generated console password for user `jarvis`). On the Pi, first boot moves them to `/etc/jarvis/secrets.env`
  (root-only), wipes the copy on the boot partition, and drops the Tailscale key and console password once used.
- Tailscale key: login.tailscale.com/admin/settings/keys > Generate auth key, **one-off**, **Pre-approved** on.
  Without one, the Pi still boots; finish with `sudo tailscale up --ssh --hostname=hal9000` over the LAN
  (`ssh jarvis@hal9000.local`).
- No MQTT password: the agent is installed but stopped. Add it later with `sudo systemctl edit jarvis-agent`
  (`[Service]` / `Environment="MQTT_PASS=..."`), then `sudo systemctl enable --now jarvis-agent`.
- Pi 5: the kit sets `usb_max_current_enable=1` so the drive gets full USB power. Use the official 27 W supply.
- Pi 4 bought before late 2020 may need its bootloader updated for USB boot (Imager > Misc utility images >
  Bootloader > USB Boot, written to any SD card, boot once).
- A Claude session on the rig can stage everything without the flashing part:
  `& ([scriptblock]::Create((irm -Headers @{...} '<url above>'))) -PrepareOnly` with `PI_WIFI_SSID`,
  `PI_WIFI_PASSWORD`, `PI_TS_AUTHKEY`, `PI_MQTT_PASS` set in the environment. Re-running the paste later only flashes.
