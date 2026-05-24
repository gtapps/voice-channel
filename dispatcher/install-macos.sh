#!/bin/bash
# Install voice-dispatcher on macOS (critical-path).
# Requires Homebrew.  Run once as the operator user.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$HOME/.local/share/voice-dispatcher/venv"
LOG_DIR="$HOME/Library/Logs/voice-dispatcher"
PLIST_SRC="$SCRIPT_DIR/install/voice-dispatcher.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.gtapps.voice-dispatcher.plist"

echo "==> voice-dispatcher macOS install"

# System deps
if ! command -v brew &>/dev/null; then
  echo "Error: Homebrew not found. Install from https://brew.sh then re-run." >&2
  exit 1
fi
echo "--> brew install portaudio (for sounddevice)"
brew install portaudio

# Python venv
echo "--> creating venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
echo "--> installing voice-dispatcher"
"$VENV_DIR/bin/pip" install --quiet "$SCRIPT_DIR"

# Piper voices directory
VOICES_DIR="$HOME/.local/share/voice-dispatcher/voices"
mkdir -p "$VOICES_DIR"
echo "--> voice directory: $VOICES_DIR"
echo "    Download .onnx voice files from:"
echo "    https://github.com/rhasspy/piper/releases"

# Config skeleton
CONFIG_DIR="$HOME/.config/voice-dispatcher"
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
  cp "$SCRIPT_DIR/config.yaml.example" "$CONFIG_DIR/config.yaml"
  echo "--> config written to $CONFIG_DIR/config.yaml  (edit before starting)"
fi

# LaunchAgent
mkdir -p "$LOG_DIR"
sed \
  -e "s|VENV_PYTHON_PLACEHOLDER|$VENV_DIR/bin/python|g" \
  -e "s|REPLACE_LOG_DIR|$LOG_DIR|g" \
  "$PLIST_SRC" > "$PLIST_DST"
echo "--> LaunchAgent written to $PLIST_DST"

launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || true
echo "--> loaded with launchctl"

echo ""
echo "✓ voice-dispatcher installed."
echo ""
echo "IMPORTANT — mic permission:"
echo "  On first run, macOS will show a 'voice-dispatcher wants to use the microphone'"
echo "  dialog.  Click 'Allow'.  If you missed it:"
echo "  System Settings → Privacy & Security → Microphone → enable voice-dispatcher"
echo ""
echo "Next steps:"
echo "  1. Edit $CONFIG_DIR/config.yaml"
echo "  2. voice-dispatcher config add-hermit jarvis --triggers 'hey jarvis,hermit' --voice en_US-lessac-medium.onnx"
echo "  3. Restart: launchctl kickstart -k gui/$(id -u)/com.gtapps.voice-dispatcher"
