# voice-channel

![CI](https://github.com/gtapps/voice-channel/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/github/license/gtapps/voice-channel)
![Downloads](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/gtapps/voice-channel/_gh_traffic_stats/.github/badges/clones.json)
![Views](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/gtapps/voice-channel/_gh_traffic_stats/.github/badges/views.json)

> Hands-free voice trigger & control for Claude Code. Fully local STT + TTS. No cloud.

A Claude Code **[channel plugin](https://code.claude.com/docs/en/channels)**. Think Siri, Alexa, or
Google Home but local, and pointed at your Claude Code instances. Speak a trigger phrase + what
you want, and Claude replies aloud.

**Compatibility:** Linux ✅ · macOS & Windows (WSL2) — should work, unverified

## Architecture

Two components, one protocol:

```
LAPTOP (Linux / macOS / Windows-via-WSL2 — operator's device, mic + speakers)
  voice-dispatcher (Python)
    ├── Silero VAD + faster-whisper-tiny  — local STT, no cloud
    ├── Piper TTS                          — local TTS, no cloud
    └── WebSocket server :7355 (LAN / 0.0.0.0)

          ↕  ws://laptop.local:7355  (home LAN)
             or ws://<docker-bridge-gateway>:7355  (same host)

TARGET MACHINE (where Claude Code runs — Linux + Docker, or the same laptop)
  Claude Code session
    └── voice-channel plugin (TypeScript / Bun)
          └── MCP channel server ↔ dispatcher WebSocket
```

The **dispatcher** is the host-side service that owns your mic and speakers and routes
voice to and from your agents.

## What you need

- **Dispatcher (laptop):** Python 3.11+, [pipx](https://pipx.pypa.io), PortAudio (system audio),
  a mic + speakers, Whisper model + one Piper voice.
- **Plugin (where Claude Code runs):** Like the official Claude Code Channels Plugins: [Bun](https://bun.sh) on `PATH`, plus network reach
  to the dispatcher (LAN or localhost).

## Setup

### 1. Install the dispatcher

Install the system audio library, then the dispatcher. It stays off until you start it in step 4; `pipx uninstall voice-dispatcher` removes it.

```bash
# macOS
brew install portaudio
pipx install "git+https://github.com/gtapps/voice-channel.git#subdirectory=dispatcher"
```

```bash
# Linux / Windows (WSL2)
sudo apt install portaudio19-dev pipewire-bin
pipx install "git+https://github.com/gtapps/voice-channel.git#subdirectory=dispatcher"
```

### 2. Download a Piper voice

```bash
VOICES=~/.local/share/voice-dispatcher/voices
mkdir -p "$VOICES"
curl -L -o "$VOICES/en_US-lessac-medium.onnx" \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx
curl -L -o "$VOICES/en_US-lessac-medium.onnx.json" \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

Browse other [piper voices](https://github.com/rhasspy/piper/blob/master/VOICES.md). The Whisper STT model downloads automatically on first run.

### 3. Create an agent

```bash
voice-dispatcher config add-agent jarvis \
  --triggers "hey jarvis,jarvis,ó jarvis" \
  --voice en_US-lessac-medium.onnx
```

> The command prints an auth token. **Copy it** — you'll need it in step 6.

### 4. Start the dispatcher

```bash
voice-dispatcher run     # foreground — Ctrl-C to stop
```

Verify: `lsof -i :7355` should show a `python` process on `0.0.0.0:7355`.

<details>
<summary>Make it persistent on boot (optional)</summary>

**Linux (systemd user service):**

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

**macOS (launchd):** see [dispatcher/README.md](dispatcher/README.md#run-it-always-on).

</details>

### 5. Install the plugin

Run these **where Claude Code Agent runs** (same laptop, another LAN machine, or a container):

```bash
claude plugin marketplace add gtapps/voice-channel
claude plugin install voice@voice-channel --scope local
```

### 6. Configure the plugin

Boot Claude Code inside the folder your agent and configure the voice plugin:

```bash
/voice:configure
```

You will be prompt to answer the following:

| Prompt         | Value                                                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------ |
| Dispatcher URL | `ws://127.0.0.1:7355` _(same machine)_ — see [URL table](#dispatcher-url--where-claude-code-runs) for other setups |
| Token          | The token from step 3                                                                                              |
| Agent ID       | `jarvis`                                                                                                           |

<details>
<summary>Docker (same host): find the bridge-gateway IP</summary>

When Claude Code runs in a container on the same host as the dispatcher, `localhost` won't reach the host. Find the gateway from inside the container:

```bash
ip route show default | awk '{print $3}'
```

Use the result as `ws://<bridge-ip>:7355` (typically `172.17.0.1` or `172.18.0.1`).

</details>

### 7. Launch Claude Code with voice

```bash
cd /path/to/your/project
claude --dangerously-load-development-channels plugin:voice@voice-channel
```

`voice-channel` is a community plugin so the `--dangerously-load-development-channels` flag is required. Make sure the dispatcher (step 4) is running first.

### 8. Test it

Say **"hey jarvis, what time is it?"** — Claude should reply aloud.

> Nothing happening? Run `/voice:status` in your Claude Code session for a quick diagnostic, or see [Troubleshooting](#troubleshooting).

## Dispatcher URL — where Claude Code runs

The dispatcher binds `0.0.0.0:7355` by default, so the only thing that changes between
setups is the host in the URL:

| Where Claude Code runs                                | Dispatcher URL                                                                                                                   |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Same machine** as the dispatcher (no Docker)        | `ws://localhost:7355` (or `ws://127.0.0.1:7355`)                                                                                 |
| **Separate LAN machine** (typical multi-device setup) | `ws://laptop.local:7355` (mDNS) or `ws://192.168.x.y:7355` (static IP)                                                           |
| **Docker on the same host** as the dispatcher         | `ws://<bridge-gateway>:7355` — find with the snippet in [step 6](#6-configure-the-plugin); typically `172.17.0.1` / `172.18.0.1` |

## Multiple agents on the same machine

Each Claude Code instance connects to the dispatcher with its own `agent_id`. If you run two
instances on the same box they must have different IDs — otherwise the dispatcher can't tell them
apart.

1. **Register each agent on the dispatcher (laptop):**

   ```bash
   voice-dispatcher config add-agent alpha --triggers "hey alpha" --voice alpha.onnx
   voice-dispatcher config add-agent beta  --triggers "hey beta"  --voice beta.onnx
   ```

2. **Give each instance its own config dir** by adding `VOICE_STATE_DIR` to the project's
   `.claude/settings.local.json` (gitignored, machine-local):

   ```json
   {
     "env": {
       "VOICE_STATE_DIR": "/home/you/.claude/channels/voice-beta"
     }
   }
   ```

   Use a different path per project (`voice-alpha`, `voice-beta`, …). Claude Code injects `env`
   into every MCP server it spawns, so the plugin and both skills see it automatically. Without
   this, all instances share `~/.claude/channels/voice/` and the same `agent_id`.

3. **Run `/voice:configure` once per instance.** The skill picks up `VOICE_STATE_DIR`
   automatically and writes the config to the right place.

## Troubleshooting

> Nothing working? Run `/voice:status` inside your Claude Code session — it shows connection state, last utterance, and any errors.

## Security

The dispatcher binds `0.0.0.0:7355` and authenticates with a bearer token in the `hello` message.
Trust model: **home LAN is trusted** (WPA2/WPA3 WiFi, no port-forwarding, single-user setup).

Unlike the official bot channels' per-sender pairing/allowlist, the voice channel authenticates the
**dispatcher** via a shared token; "who can talk to it" is governed by physical mic access and your
LAN, not a sender list.

Upgrade paths (not v1, see [PROTOCOL.md](PROTOCOL.md)):

- WSS with self-signed cert + fingerprint pinning
- Tailscale/WireGuard tunnel between laptop and agent PC

### Permission relay (opt-in, OFF by default)

The voice channel can relay Claude's tool-permission prompts: the dispatcher
speaks _"Bash needs permission, say yes or no followed by alpha bravo…"_ and you
answer by voice. The 5-letter request id must be spoken, so a bare "yes" from a TV
won't approve anything. Two caveats before enabling it (`enable_permission_relay` in both
config.yaml and `/voice:configure`):

- **The mic doesn't authenticate the speaker** — anyone it can hear (a housemate, a TV) could approve.
- **You approve blind to the arguments** — the dispatcher speaks the _tool name_ ("Bash"), not the
  command itself. Pair it with `--allowedTools` to bound what voice can approve.

The local terminal dialog is always the fallback. See dispatcher/README.md for details.

## Privacy

All STT and TTS are local — no data leaves your network. However, Silero VAD + Whisper-tiny
transcribes **every detected speech segment** before the trigger-match decides whether to forward.
Non-matching transcripts are discarded immediately and never leave the dispatcher process.
This is not the same as wake-word spotting (which would only transcribe on a model hit).

## Uninstall

**Stop the dispatcher** (only if you set it up to run on boot):

```bash
# Linux (systemd)
systemctl --user disable --now voice-dispatcher
rm ~/.config/systemd/user/voice-dispatcher.service

# macOS (launchd)
launchctl unload ~/Library/LaunchAgents/voice-dispatcher.plist
rm ~/Library/LaunchAgents/voice-dispatcher.plist
```

**Remove the dispatcher:**

```bash
pipx uninstall voice-dispatcher
```

**Remove the plugin** (run where Claude Code runs):

```bash
claude plugin uninstall voice@voice-channel
rm -rf ~/.claude/channels/voice/
```

**Remove downloaded models** (optional — the Piper voice + Whisper model):

```bash
rm -rf ~/.local/share/voice-dispatcher/
```

## Protocol

See [PROTOCOL.md](PROTOCOL.md) for the full WebSocket message schema.

## License

Apache-2.0
