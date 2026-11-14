#!/usr/bin/env bash
# Deploy the AI pet to the Raspberry Pi (idempotent). Usage: ./deploy/deploy-pi.sh [ssh-host]
# The pet is chosen in config.toml (character = "…"); its memory lives in ~/.aipet/pets/<name>/ on the Pi.
set -euo pipefail
HOST="${1:-raspberrypi.local}"
cd "$(dirname "$0")/.."

echo "== System packages (PortAudio for microphone/speaker)"
ssh "$HOST" 'sudo apt-get install -y -q portaudio19-dev libportaudio2 python3-dev build-essential rsync git >/dev/null'

echo "== uv"
ssh "$HOST" 'command -v ~/.local/bin/uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'

echo "== Code"
rsync -az --delete \
  --exclude .venv --exclude .git --exclude __pycache__ --exclude 'frontend/node_modules' --exclude .ruff_cache --exclude .pytest_cache \
  ./ "$HOST":aipet/

echo "== Google Cloud credentials (ADC)"
ssh "$HOST" 'mkdir -p ~/.config/gcloud'
ADC=~/.config/gcloud/application_default_credentials.json  # only needed if a Google/Vertex engine is used
[ -f "$ADC" ] && scp -q "$ADC" "$HOST":.config/gcloud/ || echo "  (no local ADC credentials: skipped)"

echo "== Python dependencies"
ssh "$HOST" 'cd ~/aipet && ~/.local/bin/uv sync --frozen'

echo "== systemd service"
# The service used to be called "flocon" (code in ~/flocon): stop and disable it, keep its files.
ssh "$HOST" 'if systemctl list-unit-files flocon.service >/dev/null 2>&1; then
    sudo systemctl disable --now flocon 2>/dev/null || true
    sudo rm -f /etc/systemd/system/flocon.service
  fi
  sudo cp ~/aipet/deploy/aipet.service /etc/systemd/system/aipet.service
  sudo systemctl daemon-reload && sudo systemctl enable --now aipet && sudo systemctl restart aipet'
echo "OK — logs: ssh $HOST journalctl -u aipet -f"
