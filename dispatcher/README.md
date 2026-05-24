# voice-dispatcher

Host service for voice control — runs on the operator's laptop, owns the mic and speakers.
Listens for trigger phrases, transcribes via Whisper-tiny, and routes commands to agent
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

### 2. Register an agent

```bash
voice-dispatcher config add-agent jarvis \
  --triggers "hey jarvis,agent,ó agent" \
  --voice en_US-lessac-medium.onnx
```

Copy the printed token. Inside the agent's container, run `/voice:configure` with this token.

Adding an agent is a **3-step recipe** — not a one-YAML-line operation:
1. `voice-dispatcher config add-agent <id> ...` (this command)
2. `claude plugin install voice@voice-channel --scope local` inside that agent's container
   (first add the marketplace once: `claude plugin marketplace add gtapps/voice-channel`)
3. `/voice:configure` inside that agent's container (dispatcher URL + token)

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
voice-dispatcher config add-agent <id> ...     # register an agent
voice-dispatcher config list                    # list registered agents
voice-dispatcher config rotate-token <id>       # generate a new token
voice-dispatcher list-devices                   # list audio devices
```

## AirPods / Bluetooth headset note

When a Bluetooth headset activates as a **microphone**, it switches to the HFP/SCO
codec (mono, 8–16 kHz). This both degrades Whisper-tiny accuracy *and* makes the
headset's audio **output** unreliable (HFP output on Linux is flaky/silent).

**Recommended on both platforms:** built-in mic for **input**, headset for **output only**.

**macOS:** set `audio.input_device` to `null` (built-in) and `audio.output_device`
to the AirPods index from `voice-dispatcher list-devices`.

**Linux (PipeWire):** leave both devices `null` and pin routing with `pactl` so the
headset stays in high-fidelity A2DP (output-only):
```bash
# Use the built-in mic for input so the headset isn't pulled into HFP:
pactl set-default-source alsa_input.<builtin>      # see: pactl list sources short
# Keep the headset in A2DP (high-quality output):
pactl set-card-profile bluez_card.<addr> a2dp-sink # see: pactl list cards short
```
The dispatcher plays TTS via `pw-play`, which follows the PipeWire default sink, so
`output_device: null` routes correctly to the headset. If the built-in mic clips on
ambient noise, lower its gain: `pactl set-source-volume <source> 20%`.

> These `pactl` settings reset on reboot. Re-run them (or add to a login script)
> for persistence — a future version may apply them automatically.

## Permission relay (opt-in, OFF by default)

When `enable_permission_relay: true` for an agent (config.yaml) **and** the plugin
is configured with it on (`/voice:configure`), the dispatcher relays Claude's
tool-permission prompts through voice:

1. Claude requests permission (e.g. to run `Bash`).
2. The dispatcher speaks: *"Bash needs permission. Say yes or no, followed by alpha bravo charlie delta echo."*
3. You reply *"yes alpha bravo charlie delta echo"* (or *"no …"*). The 5-letter
   request id **must** be spoken — a bare "yes" is ignored so ambient speech can't
   approve a tool call. You may speak the id phonetically or as plain letters.
4. The verdict goes back to Claude. The local terminal dialog is always available
   as a fallback; the voice window times out after 30 s.

**Security:** the mic does not authenticate the speaker. Only enable this if you
accept that anyone the mic can hear (a TV, a housemate) could approve a tool call.

## Laptop sleep

Voice is unavailable when the lid is closed. For long-haul sessions:
```bash
caffeinate -i &   # keep laptop awake while this shell is open
```

The plugin's reconnect loop handles dispatcher reappearance automatically.

## Protocol

See [../PROTOCOL.md](../PROTOCOL.md) for the full WebSocket message schema.
