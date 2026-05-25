#!/bin/bash
# Install voice-dispatcher on macOS (should work; not yet verified).
# Requires Homebrew.  Run once as the operator user.
# Usage:  ./install-macos.sh            # install only (run in the foreground)
#         ./install-macos.sh --daemon   # also register an always-on LaunchAgent
set -euo pipefail

# --daemon: also register a launchd LaunchAgent that starts at login.
# Default: install only — run in the foreground with `voice-dispatcher run`.
INSTALL_DAEMON=false
for arg in "$@"; do
  case "$arg" in
    --daemon) INSTALL_DAEMON=true ;;
    *) echo "Unknown option: $arg (use --daemon for an always-on service)" >&2; exit 1 ;;
  esac
done

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

# LaunchAgent (only with --daemon)
if $INSTALL_DAEMON; then
  mkdir -p "$LOG_DIR"
  sed \
    -e "s|VENV_PYTHON_PLACEHOLDER|$VENV_DIR/bin/python|g" \
    -e "s|REPLACE_LOG_DIR|$LOG_DIR|g" \
    "$PLIST_SRC" > "$PLIST_DST"
  echo "--> LaunchAgent written to $PLIST_DST"
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || true
  echo "--> loaded with launchctl"
fi

echo ""
echo "✓ voice-dispatcher installed."
echo ""
echo "IMPORTANT — mic permission:"
echo "  On first run, macOS will show a 'voice-dispatcher wants to use the microphone'"
echo "  dialog.  Click 'Allow'.  If you missed it:"
echo "  System Settings → Privacy & Security → Microphone → enable voice-dispatcher"
echo ""
echo "Next steps:"
echo "  1. Register an agent (prints a token for /voice:configure):"
echo "     $VENV_DIR/bin/voice-dispatcher config add-agent jarvis --triggers 'hey jarvis,agent' --voice en_US-lessac-medium.onnx"
echo "  2. Download a Piper .onnx voice into the voices dir (see README)."
if $INSTALL_DAEMON; then
  echo "  3. (Re)start the service: launchctl kickstart -k gui/$(id -u)/com.gtapps.voice-dispatcher"
else
  echo "  3. Run it (foreground, Ctrl-C to stop):"
  echo "     $VENV_DIR/bin/voice-dispatcher run"
  echo ""
  echo "  Want always-on instead? Re-run: ./install-macos.sh --daemon"
fi
