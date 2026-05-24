#!/bin/bash
# Install voice-dispatcher on a Linux laptop (acceptance-gated alternate).
# Tested on Ubuntu/Debian with PulseAudio or PipeWire.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$HOME/.local/share/voice-dispatcher/venv"
SERVICE_SRC="$SCRIPT_DIR/install/voice-dispatcher.service"
SERVICE_DST="$HOME/.config/systemd/user/voice-dispatcher.service"

echo "==> voice-dispatcher Linux install"

# System deps
echo "--> installing system packages"
sudo apt-get install -y --no-install-recommends \
  portaudio19-dev \
  python3-venv \
  python3-dev \
  avahi-daemon \
  avahi-utils

# Python venv
echo "--> creating venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
echo "--> installing voice-dispatcher"
"$VENV_DIR/bin/pip" install --quiet "$SCRIPT_DIR"

# Piper voices
VOICES_DIR="$HOME/.local/share/voice-dispatcher/voices"
mkdir -p "$VOICES_DIR"

# Config skeleton
CONFIG_DIR="$HOME/.config/voice-dispatcher"
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
  cp "$SCRIPT_DIR/config.yaml.example" "$CONFIG_DIR/config.yaml"
  echo "--> config written to $CONFIG_DIR/config.yaml"
fi

# systemd user service
mkdir -p "$(dirname "$SERVICE_DST")"
sed -e "s|VENV_PYTHON_PLACEHOLDER|$VENV_DIR/bin/python|g" \
    "$SERVICE_SRC" > "$SERVICE_DST"
systemctl --user daemon-reload
systemctl --user enable --now voice-dispatcher.service
echo "--> systemd user service enabled and started"

echo ""
echo "✓ voice-dispatcher installed."
echo ""
echo "Status:  systemctl --user status voice-dispatcher"
echo "Logs:    journalctl --user -u voice-dispatcher -f"
