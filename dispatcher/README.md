# voice-dispatcher

Host service for voice control — runs on the operator's laptop, owns the mic and speakers.
Listens for trigger phrases, transcribes via Whisper-tiny, and routes commands to hermit
containers over WebSocket.

## Requirements

- **macOS laptop** (v1 critical-path): Apple Silicon or Intel, built-in mic or AirPods
  _(use built-in mic for input — see AirPods note below)_
- Python 3.11+
- ~500 MB disk for Whisper weights + Piper voice files

Linux laptop is v1 acceptance-gated (may slip to v1.1 if testing reveals blocking issues).

## Install

### macOS (critical-path)

```bash
cd dispatcher
./install-macos.sh
```

On first start, macOS will ask for microphone permission. Click **Allow**.  
If you missed the dialog: System Settings → Privacy & Security → Microphone → enable voice-dispatcher.

### Linux laptop (acceptance-gated)

```bash
cd dispatcher
./install-linux.sh
```

## Configure

### 1. Edit config.yaml

```bash
$EDITOR ~/.config/voice-dispatcher/config.yaml
```

Key settings:
- `audio.input_device` — leave `null` to use system default (recommended: built-in mic)
- `audio.output_device` — leave `null` to use system default (AirPods for output is fine)
- `whisper.model` — `tiny` is the default; `base` improves accuracy at ~2× CPU cost

### 2. Register a hermit

```bash
voice-dispatcher config add-hermit jarvis \
  --triggers "hey jarvis,hermit,ó hermit" \
  --voice en_US-lessac-medium.onnx
```

Copy the printed token. Inside the hermit's container, run `/voice:configure` with this token.

Adding a hermit is a **3-step recipe** — not a one-YAML-line operation:
1. `voice-dispatcher config add-hermit <id> ...` (this command)
2. `/plugin install voice-channel` inside that hermit's container
3. `/voice:configure` inside that hermit's container (dispatcher URL + token)

### 3. Download a Piper voice

```bash
VOICES=~/.local/share/voice-dispatcher/voices
# English (US), medium quality, ~60 MB:
curl -L -o "$VOICES/en_US-lessac-medium.onnx" \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx
curl -L -o "$VOICES/en_US-lessac-medium.onnx.json" \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

Browse all voices: https://github.com/rhasspy/piper/blob/master/VOICES.md

### 4. Start

**macOS:**
```bash
launchctl kickstart -k gui/$(id -u)/com.gtapps.voice-dispatcher
```

**Linux:**
```bash
systemctl --user restart voice-dispatcher
```

**Standalone (audio + core, no WebSocket — for testing):**
```bash
python -m voice_dispatcher run --no-adapter
```

## CLI reference

```bash
voice-dispatcher run                            # start everything
voice-dispatcher run --no-adapter               # audio+core only (M2 verification)
voice-dispatcher config add-hermit <id> ...     # register a hermit
voice-dispatcher config list                    # list registered hermits
voice-dispatcher config rotate-token <id>       # generate a new token
voice-dispatcher list-devices                   # list audio devices
```

## AirPods note

When AirPods activate as a microphone, Bluetooth switches to the SCO codec  
(mono, 8–16 kHz), which significantly degrades Whisper-tiny accuracy.

**Recommended:** use the laptop's **built-in mic for input**, AirPods for output only.  
Set `audio.input_device` to `null` (system default built-in) and  
`audio.output_device` to the AirPods index (`voice-dispatcher list-devices`).

## Laptop sleep

Voice is unavailable when the lid is closed. For long-haul sessions:
```bash
caffeinate -i &   # keep laptop awake while this shell is open
```

The plugin's reconnect loop handles dispatcher reappearance automatically.

## Protocol

See [../PROTOCOL.md](../PROTOCOL.md) for the full WebSocket message schema.
