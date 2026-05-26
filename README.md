# voice-channel

![CI](https://github.com/gtapps/voice-channel/actions/workflows/ci.yml/badge.svg)
![Version](https://img.shields.io/github/v/release/gtapps/voice-channel)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Downloads](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/gtapps/voice-channel/_gh_traffic_stats/.github/badges/clones.json)
![Views](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/gtapps/voice-channel/_gh_traffic_stats/.github/badges/views.json)

> Hands-free voice trigger & control for Claude Code. Fully local STT + TTS. No cloud.

A Claude Code **[channel plugin](https://code.claude.com/docs/en/channels)**. Think Siri, Alexa, or
Google Home but local, and pointed at your Claude Code instances. Speak a trigger phrase + what
you want, and Claude replies aloud. Multi-agent routing support across local network & docker.

**Compatibility:** Linux ✅ · Windows (WSL2) ✅ · macOS — should work, unverified

## Architecture

Two components, one protocol:

```
LAPTOP (Linux / macOS / Windows-via-WSL2 — operator's device, mic + speakers)
  voice-dispatcher (Python)
    ├── Silero VAD + faster-whisper-tiny  — local STT, no cloud
    ├── Piper TTS                          — local TTS, no cloud
    └── WebSocket server :7355 (LAN / 0.0.0.0) — wss:// with self-signed cert

          ↕  wss://laptop.local:7355  (home LAN — TLS + cert-pinned)
             or wss://<docker-bridge-gateway>:7355  (same host)

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

## Installation model: what runs where?

For a single Claude Code session on the same machine, follow the setup straight through. For
multiple Claude Code sessions, or Claude Code running in another folder/container/machine, keep
these scopes separate:

| Thing              | Location                                                  | How many times?                               |
| ------------------ | --------------------------------------------------------- | --------------------------------------------- |
| `voice-dispatcher` | Machine with mic + speakers                               | Once per audio host                           |
| Piper voice files  | Same machine as the dispatcher                            | Once per voice                                |
| Dispatcher agent   | On the dispatcher                                         | Once per Claude Code voice identity           |
| Claude Code plugin | Each Claude Code environment/project where you want voice | Usually once per project with `--scope local` |
| `/voice:configure` | Inside each Claude Code session/project                   | Once per instance/config dir                  |

The default plugin state directory is `~/.claude/channels/voice/`. If you run more than one
Claude Code voice instance on the same machine, give each one a different dispatcher agent ID,
trigger phrase, and `VOICE_STATE_DIR`; otherwise they will share the same `agent_id` and token.

## Setup

### 1. Install the dispatcher

Install the system audio library, then the dispatcher. It stays off until you start it in step 4; `pipx uninstall voice-dispatcher` removes it.

```bash
# macOS
brew install portaudio pipx
pipx install "git+https://github.com/gtapps/voice-channel.git#subdirectory=dispatcher"
```

```bash
# Linux
sudo apt install portaudio19-dev pipewire-bin pipx
pipx install "git+https://github.com/gtapps/voice-channel.git#subdirectory=dispatcher"
```

```bash
# Windows (WSL2)
sudo apt install portaudio19-dev pipewire-bin pipx sox libsox-fmt-pulse libasound2-plugins pulseaudio unzip
pipx ensurepath   # restart your shell afterwards so pipx-installed commands land on PATH
pipx install "git+https://github.com/gtapps/voice-channel.git#subdirectory=dispatcher"
```

If `voice-dispatcher` is not found after install, run `pipx ensurepath` and restart your shell.

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

> The command auto-generates a TLS cert (first run only) and prints a **pairing string** like
> `voicepair_eyJhZ2VudF9pZCI6Imp...`. **Copy it** — you'll paste it into `/voice:configure` in step 7.

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

Run these **where Claude Code Agent runs** (same laptop, another LAN machine, or a container).

The plugin runtime is [Bun](https://bun.sh). If it's not already on `PATH` (e.g. a fresh WSL2 or
Linux box), install it first — on WSL2 this needs `unzip` from [step 1](#1-install-the-dispatcher):

```bash
curl -fsSL https://bun.sh/install | bash   # then restart your shell
```

Then install the plugin:

```bash
claude plugin marketplace add gtapps/voice-channel
claude plugin install voice@voice-channel --scope local
```

### 6. Launch Claude Code with voice

```bash
claude --dangerously-load-development-channels plugin:voice@voice-channel
```

`voice-channel` is a community plugin so the `--dangerously-load-development-channels` flag is required. Make sure the dispatcher (step 4) is running first.

### 7. Configure the plugin

Inside a Claude Code session, run:

```bash
/voice:configure
```

You will be prompted to answer:

| Prompt         | Value                                                                                                               |
| -------------- | ------------------------------------------------------------------------------------------------------------------- |
| Dispatcher URL | `wss://127.0.0.1:7355` _(same machine)_ — see [URL table](#dispatcher-url--where-claude-code-runs) for other setups |
| Pairing string | The `voicepair_...` string printed in step 3 (bundles agent ID, token, and pinned TLS cert in one paste)            |

The skill writes the **bearer token** to `~/.claude/channels/voice/.env` (chmod 600 — it's a credential) and the rest of the config to `config.json`.

After `/voice:configure`, restart Claude Code with the same channel flag so the MCP server starts
with the new config:

```bash
claude --dangerously-load-development-channels plugin:voice@voice-channel
```

<details>
<summary>Docker (same host): find the bridge-gateway IP</summary>

When Claude Code runs in a container on the same host as the dispatcher, `localhost` won't reach the host. Find the gateway from inside the container:

```bash
ip route show default | awk '{print $3}'
```

Use the result as `wss://<bridge-ip>:7355` (typically `172.17.0.1` or `172.18.0.1`).

</details>

### 8. Test it

Say **"hey jarvis, what time is it?"** — Claude should reply aloud.

> Nothing happening? Run `/voice:status` in your Claude Code session for a quick diagnostic, or see [Troubleshooting](#troubleshooting).

## Dispatcher URL — where Claude Code runs

The dispatcher binds `0.0.0.0:7355` by default, so the only thing that changes between
setups is the host in the URL:

| Where Claude Code runs                                | Dispatcher URL                                                                                                                    |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| **Same machine** as the dispatcher (no Docker)        | `wss://127.0.0.1:7355`                                                                                                            |
| **Separate LAN machine** (typical multi-device setup) | `wss://192.168.x.y:7355` (static IP) or `wss://laptop.local:7355` (mDNS)                                                          |
| **Docker on the same host** as the dispatcher         | `wss://<bridge-gateway>:7355` — find with the snippet in [step 7](#7-configure-the-plugin); typically `172.17.0.1` / `172.18.0.1` |

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

### Add voice to another Claude Code folder

If the dispatcher and plugin are already installed, adding voice to another local folder is mostly
about creating a new dispatcher identity and isolating plugin state:

```bash
voice-dispatcher config add-agent beta \
  --triggers "hey beta" \
  --voice en_US-lessac-medium.onnx
```

In the second project's `.claude/settings.local.json`:

```json
{
  "env": {
    "VOICE_STATE_DIR": "/home/you/.claude/channels/voice-beta"
  }
}
```

Then start Claude Code in that folder with the voice channel flag, run `/voice:configure`, and paste
the `voicepair_...` string for `beta`.

## Troubleshooting

> Nothing working? Run `/voice:status` inside your Claude Code session — it shows connection state, last utterance, and any errors.

First checks:

```bash
voice-dispatcher config list
voice-dispatcher run
```

Then inside Claude Code:

```bash
/voice:status
```

Common causes:

- Dispatcher is not running
- Wrong dispatcher URL for Docker or LAN
- Stale pairing string after TLS or token rotation
- Bun is not on `PATH` where Claude Code runs

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for deeper diagnostics.

## Security

### TLS & pairing

The dispatcher serves **`wss://`** with a self-signed certificate auto-generated on first run. The
plugin **pins the dispatcher's public certificate PEM in-band** before sending its bearer token — so
the token is never transmitted to an impersonator occupying the same host/port.

Identity is split:

- **Cert** (shared, per-dispatcher) — answers _"is this the real dispatcher?"_
- **Token** (per-agent, secret) — answers _"which agent is this?"_

The token is stored in `~/.claude/channels/voice/.env` at `chmod 600` (it's a credential). The
cert PEM and fingerprint are stored in `config.json` (they are public certificate material).

**How pairing works:**

1. `voice-dispatcher config add-agent <id> ...` → auto-provisions cert, prints one `voicepair_...` string
2. Paste it into `/voice:configure` → decoded by Bun, writes `.env` + `config.json`
3. On connect, the plugin opens one WSS connection that trusts only the pinned cert PEM and sends
   the token only after that TLS handshake succeeds

**What pinning protects against:** passive eavesdropping and dispatcher
impersonation/misdirection — your token is not sent unless the WSS handshake validates against the
pinned cert. The plugin pins cert identity directly and intentionally ignores hostname matching so
localhost, LAN IP, mDNS, and Docker bridge URLs can share the same dispatcher cert.

**Cert management:**

```bash
voice-dispatcher tls fingerprint   # print current fingerprint
voice-dispatcher tls rotate        # replace cert — all agents must re-pair
voice-dispatcher config rotate-token <id>  # rotate one agent's token (cert unchanged)
```

### Per-agent bearer token

Each agent gets its own token. Rotating one agent's token (via `config rotate-token`) only
affects that agent; the shared cert and all other agents keep working.

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

All STT and TTS are local — no audio is sent to a cloud service. However, Silero VAD + Whisper-tiny
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
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.gtapps.voice-dispatcher.plist
rm ~/Library/LaunchAgents/com.gtapps.voice-dispatcher.plist
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
