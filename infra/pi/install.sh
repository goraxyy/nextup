#!/usr/bin/env bash
# Install the NextUp stall agent on a Raspberry Pi. Run it from the repo, as your normal user:
#   ./infra/pi/install.sh
# Safe to run again after a git pull.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ME="${SUDO_USER:-$USER}"

sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-gpiozero python3-lgpio
# --system-site-packages: use apt's gpiozero and lgpio (the HC-SR04 driver) inside the venv
python3 -m venv --system-site-packages "$REPO/.venv-pi"
"$REPO/.venv-pi/bin/pip" install -q --upgrade pip
"$REPO/.venv-pi/bin/pip" install -q -r "$REPO/device/requirements.txt"

sudo mkdir -p /etc/nextup /var/lib/nextup
sudo chown "$ME" /var/lib/nextup
if [ ! -f /etc/nextup/agent.env ]; then
  sudo install -m 600 -o "$ME" "$REPO/infra/pi/agent.env.example" /etc/nextup/agent.env
  echo ">>> Put the stall's token in /etc/nextup/agent.env before starting the agent."
fi
sudo usermod -aG gpio "$ME"   # drive the HC-SR04's GPIO pins

for unit in nextup-agent nextup-kiosk; do
  sed -e "s#__USER__#$ME#g" -e "s#__REPO__#$REPO#g" "$REPO/infra/pi/$unit.service" \
    | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable nextup-agent.service
echo "Installed. Start it with: sudo systemctl restart nextup-agent"
