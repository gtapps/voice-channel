# voice-dispatcher

Host service for voice control — runs on the operator's laptop, owns the mic and speakers.
Listens for trigger phrases, transcribes via Whisper-tiny, and routes commands to agent
containers over WebSocket.

## Requirements

- **A laptop with a mic + speakers.** Linux is the confirmed platform; macOS and Windows
  (via WSL2) should work but are unverified.
- Python 3.11+, [pipx](https://pipx.pypa.io), and PortAudio (system audio library).
- ~500 MB disk for the Whisper model + Piper voice files.

## Install

Install with [pipx](https://pipx.pypa.io) — isolated and reversible with
`pipx uninstall voice-dispatcher`:

```bash
sudo apt install portaudio19-dev pipewire-bin   # Linux / WSL2  ·  macOS: brew install portaudio
pipx install "git+https://github.com/gtapps/voice-channel.git#subdirectory=dispatcher"
```

- **macOS:** on first `voice-dispatcher run`, macOS asks for microphone permission — click
  **Allow** (or System Settings → Privacy & Security → Microphone → enable your terminal).
- **Windows:** install [WSL2](https://learn.microsoft.com/windows/wsl/install) and run the Linux
  steps inside it; mic passthrough needs WSLg (Windows 11, or updated Windows 10).

## Configure & run

### 1. Register an agent

```bash
voice-dispatcher config add-agent jarvis \
  --triggers "hey jarvis,jarvis,ó jarvis" \
  --voice en_US-lessac-medium.onnx
```

This creates `~/.config/voice-dispatcher/config.yaml` and prints a `voicepair_...` pairing string. Where that agent's
Claude Code runs, run `/voice:configure` with the pairing string. Each agent is a 3-step recipe:

1. `voice-dispatcher config add-agent <id> ...` (above)
2. `claude plugin install voice@voice-channel --scope local` where that agent runs
   (add the marketplace once: `claude plugin marketplace add gtapps/voice-channel`)
3. `/voice:configure` there (dispatcher URL + token)

### 2. Download a Piper voice

```bash
VOICES=~/.local/share/voice-dispatcher/voices
mkdir -p "$VOICES"
# English (US), medium quality, ~60 MB:
curl -L -o "$VOICES/en_US-lessac-medium.onnx" \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx
curl -L -o "$VOICES/en_US-lessac-medium.onnx.json" \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

Browse all voices: https://github.com/rhasspy/piper/blob/master/VOICES.md

### 3. Start

```bash
voice-dispatcher run                 # foreground; Ctrl-C to stop
voice-dispatcher run --no-adapter    # audio + core only, no WebSocket (for testing)
```

Optional audio tuning in `~/.config/voice-dispatcher/config.yaml`:
- `audio.input_device` / `audio.output_device` — leave `null` for the system default
- `whisper.model` — `tiny` (default); `base` improves accuracy at ~2× CPU cost

## Run it always-on

Keep the dispatcher running across logins instead of holding a terminal open. Both units point
at the pipx-installed `voice-dispatcher` on your `PATH`.

**Linux (systemd `--user`):**
```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/voice-dispatcher.service <<'EOF'
[Unit]
Description=voice-dispatcher
[Service]
ExecStart=%h/.local/bin/voice-dispatcher run
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now voice-dispatcher
```

**macOS (launchd):**
```bash
cat > ~/Library/LaunchAgents/com.gtapps.voice-dispatcher.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.gtapps.voice-dispatcher</string>
  <key>ProgramArguments</key>
  <array><string>$HOME/.local/bin/voice-dispatcher</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
EOF
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.gtapps.voice-dispatcher.plist
```

> Re-running `bootstrap` after the plist exists errors with _"service already exists"_ — run
> `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.gtapps.voice-dispatcher.plist` first.

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

**Security:** the mic does not authenticate the speaker, and you approve by *tool name* only —
step 2 speaks "Bash needs permission", never the command's arguments. Enable it only if you accept
that anyone the mic can hear could approve a tool call sight-unseen; pair it with `--allowedTools`
to bound what voice can approve.

## Laptop sleep

Voice is unavailable when the lid is closed. For long-haul sessions:
```bash
caffeinate -i &   # keep laptop awake while this shell is open
```

The plugin's reconnect loop handles dispatcher reappearance automatically.

## Protocol

See [../PROTOCOL.md](../PROTOCOL.md) for the full WebSocket message schema.
