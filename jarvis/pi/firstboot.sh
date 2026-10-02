#!/bin/bash
# Hal9000 (the Jarvis Pi) first boot. cloud-init runs this once (runcmd in user-data), as root, after the network is up.
# pi-flash-kit.ps1 on the rig puts it, the agent files and secrets.env in /boot/firmware/jarvis/.
#
# What it does:
#   - moves secrets.env off the boot partition into /etc/jarvis (root-only) and wipes the copy on bootfs
#   - sets the jarvis user's console password (kept on the rig, never in user-data)
#   - updates the OS and turns on unattended security updates
#   - installs Tailscale and joins the tailnet as hal9000, with Tailscale SSH on
#   - installs the Jarvis agent, which reports CPU/memory/temp/uptime to Home Assistant over MQTT
#     (only started when an MQTT password was given; otherwise left installed and disabled)
#   - turns on the hardware watchdog so a hung Pi reboots itself
#   - writes /boot/firmware/jarvis/FIRSTBOOT-RESULT.txt so the result can be read from the rig too
# Safe to re-run: sudo bash /boot/firmware/jarvis/firstboot.sh

set -u
KIT=/boot/firmware/jarvis
[ -d "$KIT" ] || KIT=/boot/jarvis
LOG=/var/log/jarvis-firstboot.log
RESULT="$KIT/FIRSTBOOT-RESULT.txt"
exec > >(tee -a "$LOG") 2>&1
echo "=== Jarvis first boot $(date -Is) ==="

declare -A STEP
ok()   { STEP[$1]="OK $2"; echo "[ok] $1: $2"; }
bad()  { STEP[$1]="FAILED $2"; echo "[!!] $1: $2"; }
retry() { local n=0; until "$@"; do n=$((n + 1)); [ $n -ge 5 ] && return 1; sleep $((n * 10)); done; }

# --- Secrets ---------------------------------------------------------------------------
install -d -m 700 /etc/jarvis
if [ -f "$KIT/secrets.env" ]; then
    tr -d '\r' < "$KIT/secrets.env" > /etc/jarvis/secrets.env
    chmod 600 /etc/jarvis/secrets.env
    shred -u "$KIT/secrets.env" 2>/dev/null || rm -f "$KIT/secrets.env"
fi
# shellcheck disable=SC1091
[ -f /etc/jarvis/secrets.env ] && . /etc/jarvis/secrets.env
JARVIS_USER=${JARVIS_USER:-jarvis}
PI_HOSTNAME=${PI_HOSTNAME:-hal9000}

if [ -n "${PI_PASSWORD:-}" ]; then
    echo "$JARVIS_USER:$PI_PASSWORD" | chpasswd && ok password "console password set" || bad password "chpasswd failed"
    sed -i '/^PI_PASSWORD=/d' /etc/jarvis/secrets.env
fi

# --- Network check ---------------------------------------------------------------------
for _ in $(seq 1 30); do getent hosts deb.debian.org >/dev/null && break; sleep 10; done
if getent hosts deb.debian.org >/dev/null; then ok network "$(hostname -I | tr ' ' '\n' | grep -v : | head -1)"; else bad network "no internet after 5 min"; fi

# --- OS updates ------------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
if retry apt-get update -q && retry apt-get -y -q -o Dpkg::Options::=--force-confold full-upgrade \
   && retry apt-get -y -q install curl python3-paho-mqtt unattended-upgrades; then
    ok updates "OS up to date, unattended security updates on"
else
    bad updates "apt failed, see $LOG"
fi

# --- Tailscale -------------------------------------------------------------------------
if ! command -v tailscale >/dev/null; then
    curl -fsSL https://tailscale.com/install.sh -o /tmp/ts-install.sh && retry sh /tmp/ts-install.sh
fi
if command -v tailscale >/dev/null; then
    systemctl enable --now tailscaled
    if [ -n "${TS_AUTHKEY:-}" ]; then
        if retry tailscale up --auth-key="$TS_AUTHKEY" --hostname="$PI_HOSTNAME" --ssh; then
            ok tailscale "joined as $PI_HOSTNAME $(tailscale ip -4 2>/dev/null | head -1)"
        else
            bad tailscale "tailscale up failed (expired or used key?). Fix: sudo tailscale up --ssh --hostname=$PI_HOSTNAME"
        fi
        sed -i '/^TS_AUTHKEY=/d' /etc/jarvis/secrets.env
    else
        bad tailscale "installed, no auth key given. Run: sudo tailscale up --ssh --hostname=$PI_HOSTNAME"
    fi
else
    bad tailscale "install failed"
fi

# --- Jarvis agent (Home Assistant over MQTT) -------------------------------------------
if [ -f "$KIT/jarvis_agent.py" ]; then
    install -d /opt/jarvis
    tr -d '\r' < "$KIT/jarvis_agent.py" > /opt/jarvis/jarvis_agent.py
    tr -d '\r' < "$KIT/jarvis-agent.service" > /etc/systemd/system/jarvis-agent.service
    sed -i "s/^User=.*/User=$JARVIS_USER/" /etc/systemd/system/jarvis-agent.service
    # paho-mqtt 2.x is needed (Trixie ships it); fall back to pip on older images.
    python3 -c 'import paho.mqtt.client as m; m.CallbackAPIVersion' 2>/dev/null \
        || pip3 install --break-system-packages "paho-mqtt>=2" || true
    cat > /etc/sudoers.d/jarvis-agent <<EOF
$JARVIS_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl restart jarvis-voice.service
EOF
    chmod 440 /etc/sudoers.d/jarvis-agent
    visudo -cf /etc/sudoers.d/jarvis-agent >/dev/null || rm -f /etc/sudoers.d/jarvis-agent
    install -d /etc/systemd/system/jarvis-agent.service.d
    # Jake named the Pi Hal9000 (2026-10-02); entity ids stay jarvis_pi_* so the HA dashboard keeps working.
    printf '[Service]\nEnvironment=JARVIS_DEVICE_NAME=Hal9000\n' > /etc/systemd/system/jarvis-agent.service.d/name.conf
    if [ -n "${MQTT_PASS:-}" ]; then
        esc=$(printf '%s' "$MQTT_PASS" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/%/%%/g')
        printf '[Service]\nEnvironment="MQTT_PASS=%s"\n' "$esc" > /etc/systemd/system/jarvis-agent.service.d/secret.conf
        chmod 600 /etc/systemd/system/jarvis-agent.service.d/secret.conf
        systemctl daemon-reload && systemctl enable --now jarvis-agent \
            && ok agent "running, shows up in Home Assistant as Hal9000" || bad agent "service failed to start"
    else
        systemctl daemon-reload
        ok agent "installed, not started (no MQTT password yet; add it with: sudo systemctl edit jarvis-agent)"
    fi
else
    bad agent "agent files missing from $KIT"
fi

# --- Hardware watchdog -----------------------------------------------------------------
install -d /etc/systemd/system.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15\nRebootWatchdogSec=2min\n' > /etc/systemd/system.conf.d/jarvis-watchdog.conf
systemctl daemon-reexec && ok watchdog "a hung Pi reboots itself"

# --- Result ----------------------------------------------------------------------------
{
    echo "Jarvis Pi first boot finished $(date -Is)"
    echo "Hostname: $(hostname)   LAN: $(hostname -I | tr ' ' '\n' | grep -v : | head -1)"
    for k in password network updates tailscale agent watchdog; do
        [ -n "${STEP[$k]:-}" ] && echo "$k: ${STEP[$k]}"
    done
    echo "Full log: $LOG"
} | tee "$RESULT"
echo "=== done ==="
