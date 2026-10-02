#!/usr/bin/env bash
# Installs Alexa Media Player into the Home Assistant container on homebase (WSL2 Docker) and
# creates the jarvis-bot service user the Windows bridge logs in with. Safe to re-run.
#   wsl -e bash /mnt/c/Jarvis/alexa/install-alexa.sh
# Needs: HA onboarding finished (Jake's owner account exists). Prints a one-line result per step.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SECRETS=/mnt/c/Jarvis/secrets
DOCKER=docker
$DOCKER ps >/dev/null 2>&1 || DOCKER="sudo -n docker"

CT=$($DOCKER ps --format '{{.Names}} {{.Image}}' | awk 'tolower($0) ~ /home-?assistant/ {print $1; exit}')
[ -n "$CT" ] || { echo "FAIL: no running Home Assistant container"; exit 1; }
CFG=$($DOCKER inspect -f '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' "$CT")
[ -d "$CFG" ] || { echo "FAIL: can't find $CT's /config folder"; exit 1; }
echo "ok: container $CT, config $CFG"

if curl -fsS http://127.0.0.1:8123/api/onboarding 2>/dev/null | grep -q '"done":false'; then
  echo "WAIT: HA onboarding isn't finished. Jake creates the owner account first."; exit 2
fi

# 1. Alexa Media Player from its latest GitHub release (no HACS, so no GitHub sign-in needed).
TMP=$(mktemp -d)
curl -fsSL -o "$TMP/alexa_media.zip" \
  https://github.com/alandtse/alexa_media_player/releases/latest/download/alexa_media.zip
mkdir -p "$CFG/custom_components"
rm -rf "$CFG/custom_components/alexa_media"
mkdir -p "$CFG/custom_components/alexa_media"
python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
  "$TMP/alexa_media.zip" "$CFG/custom_components/alexa_media"
rm -rf "$TMP"
echo "ok: alexa_media $(python3 -c "import json;print(json.load(open('$CFG/custom_components/alexa_media/manifest.json'))['version'])")"

# 2. Package + config: packages folder, keep 120 days of history for routine learning.
mkdir -p "$CFG/packages"
cp "$HERE"/packages/*.yaml "$CFG/packages/"
CONF="$CFG/configuration.yaml"
cp "$CONF" "$CONF.bak-$(date +%Y%m%d-%H%M%S)"
if ! grep -q 'packages: !include_dir_named packages' "$CONF"; then
  if grep -q '^homeassistant:' "$CONF"; then
    sed -i 's/^homeassistant:.*/homeassistant:\n  packages: !include_dir_named packages/' "$CONF"
  else
    printf '\nhomeassistant:\n  packages: !include_dir_named packages\n' >> "$CONF"
  fi
fi
if ! grep -q '^recorder:' "$CONF"; then
  printf '\nrecorder:\n  purge_keep_days: 120\n' >> "$CONF"
fi
echo "ok: package + 120-day history in configuration.yaml"

# 3. jarvis-bot service user (password lives only in C:\Jarvis\secrets on homebase).
mkdir -p "$SECRETS"
if $DOCKER exec "$CT" python -m homeassistant --script auth --config /config list 2>/dev/null | grep -qx 'jarvis-bot'; then
  echo "ok: jarvis-bot already exists"
else
  PW=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
  $DOCKER exec "$CT" python -m homeassistant --script auth --config /config add jarvis-bot "$PW" >/dev/null
  printf '{"username": "jarvis-bot", "password": "%s"}\n' "$PW" > "$SECRETS/ha-bot.json"
  rm -f "$SECRETS/ha-token.json"
  echo "ok: created jarvis-bot, password in C:\\Jarvis\\secrets\\ha-bot.json"
fi

# 4. Check config, restart so HA loads the integration, the package and the new user.
if ! $DOCKER exec "$CT" python -m homeassistant --script check_config --config /config >"$CFG/.jarvis-check.log" 2>&1; then
  echo "FAIL: config check failed, not restarting. See $CFG/.jarvis-check.log"; exit 1
fi
$DOCKER restart "$CT" >/dev/null
for i in $(seq 1 60); do
  curl -fsS -o /dev/null http://127.0.0.1:8123/manifest.json 2>/dev/null && { echo "ok: HA back up"; exit 0; }
  sleep 3
done
echo "FAIL: HA didn't come back within 3 minutes"; exit 1
