# voice-channel

![Downloads](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/gtapps/voice-channel/_gh_traffic_stats/.github/badges/clones.json)
![Views](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/gtapps/voice-channel/_gh_traffic_stats/.github/badges/views.json)

A Claude Code **[channel plugin](https://code.claude.com/docs/en/channels)** - Voice control for **any Claude Code instance**. Speak to your agents from
anywhere in the room.

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

The **dispatcher** is the laptop-side service that owns your mic and speakers and routes
voice to and from your agents; the plugin is a thin (~200 LOC) WebSocket ↔ MCP bridge.
No audio libraries or Python code enter the Claude Code environment.

## Install order

### 1. Install the dispatcher on your laptop

Pick one — both leave the dispatcher off until you start it (step 4).

**A. Quick** — foreground, no background service; best for trying it out:

```bash
sudo apt install portaudio19-dev pipewire-bin   # Linux / WSL2  ·  macOS: brew install portaudio
pipx install "git+https://github.com/gtapps/voice-channel.git#subdirectory=dispatcher"
```

Standard tooling, nothing hidden — `pipx uninstall voice-dispatcher` reverses it.

**B. Managed** — one command; sets up a venv + config:

```bash
git clone https://github.com/gtapps/voice-channel.git && cd voice-channel/dispatcher
./install-linux.sh       # macOS: ./install-macos.sh  ·  add --daemon for an always-on service
```

**Windows:** install [WSL2](https://learn.microsoft.com/windows/wsl/install) and follow the
Linux steps inside it — mic passthrough needs WSLg (Windows 11, or updated Windows 10).

### 2. Download a Piper voice

`add-agent` (next step) references a `.onnx` voice file that must already exist:

```bash
VOICES=~/.local/share/voice-dispatcher/voices
curl -L -o "$VOICES/en_US-lessac-medium.onnx" \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx
curl -L -o "$VOICES/en_US-lessac-medium.onnx.json" \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

Browse other voices: https://github.com/rhasspy/piper/blob/master/VOICES.md
(The Whisper STT model downloads automatically on first dispatcher run.)

### 3. Add an agent to the dispatcher

```bash
voice-dispatcher config add-agent jarvis \
  --triggers "hey jarvis,jarvis,ó jarvis" \
  --voice en_US-lessac-medium.onnx
```

The command prints the token. Copy it — you'll need it in step 6.

### 4. Start the dispatcher

```bash
voice-dispatcher run     # foreground; Ctrl-C to stop
```

Installed with **B** (venv, not pipx)? `voice-dispatcher` isn't on your `PATH` — use the path
the installer printed (`~/.local/share/voice-dispatcher/venv/bin/voice-dispatcher run`).

Ran **B `--daemon`**? It's already running — restart it to load the new agent:

```bash
launchctl kickstart -k gui/$(id -u)/com.gtapps.voice-dispatcher   # macOS
systemctl --user restart voice-dispatcher                          # Linux
```

Confirm it's listening: `lsof -i :7355` shows a `python` process on `0.0.0.0:7355`.

### 5. Install the plugin

Run these where Claude Code runs — the same laptop as the dispatcher, another LAN machine,
or inside an agent container:

```bash
claude plugin marketplace add gtapps/voice-channel
claude plugin install voice@voice-channel --scope local
```

> **Docker:** open a shell in the container first (`docker exec -it <container> bash`), then
> run the commands there.

### 6. Configure the plugin

**Recommended:** run `/voice:configure` inside a Claude Code session and answer the
prompts (dispatcher URL, token from step 3, agent ID `jarvis`). It writes `config.json`
to the correct location automatically — no need to know the data-dir path.

For the dispatcher URL, use `ws://localhost:7355` when the dispatcher and Claude Code run on
the **same machine**. Other setups (a separate LAN host, or Docker on the same host) use a
different host — see the [Dispatcher URL table](#dispatcher-url--where-claude-code-runs).

<details>
<summary>Docker (same host): find the bridge-gateway IP</summary>

When Claude Code runs in a container on the same machine as the dispatcher, `localhost`
won't reach the host. Find the bridge-gateway IP the container uses to reach it:

```bash
# Inside the container:
python3 -c "
import struct, socket
with open('/proc/net/route') as f:
    for line in f:
        parts = line.split()
        if parts[1] == '00000000':
            print(socket.inet_ntoa(struct.pack('<I', int(parts[2], 16))))
            break
"
```

Use the result as `ws://<bridge-ip>:7355` (typically `172.17.0.1` or `172.18.0.1`).

</details>

<details>
<summary>Manual alternative (no session needed)</summary>

```bash
# The data dir is derived as {plugin}-{marketplace}:
DATA_DIR="$HOME/.claude/plugins/data/voice-voice-channel"
mkdir -p "$DATA_DIR"
cat > "$DATA_DIR/config.json" <<EOF
{
  "dispatcher_url": "ws://localhost:7355",
  "token": "<token from step 3>",
  "agent_id": "jarvis",
  "enable_permission_relay": false
}
EOF
```

`config.json` and `status.json` must live **directly** in `$DATA_DIR` (that's what
`CLAUDE_PLUGIN_DATA` resolves to — no trailing plugin-name subdir). If a future
version changes the path derivation and `/voice:status` reports a nested `voice/`
subdir, re-run `/voice:configure` instead — it always targets the right place.

</details>

### 7. Start a Claude Code session with the voice channel

```bash
cd /path/to/your/project
claude --dangerously-load-development-channels plugin:voice@voice-channel
```

> **Docker:** run this inside the container (the same shell from step 5).

> This flag is required for community (non-Anthropic-signed) channel plugins.
> The dispatcher must be running on your laptop before Claude starts.

> **Runtime:** the plugin runs on [Bun](https://bun.sh) (same as the official
> Telegram, Discord, and iMessage channels). Its start script runs `bun install` then
> `bun server.ts`, so deps install on first launch (fast — pure-JS deps, no
> compile step). If Claude Code reports `-32000` on the very first start, the
> install was still finishing — `/plugin` → reconnect, or restart the session.
> Subsequent launches are instant. Bun must be on `PATH` (hermit containers
> include it; for a bare Claude Code environment, install from https://bun.sh).

### 8. Test

Say "hey jarvis, what time is it?" — Claude should reply aloud.

## Dispatcher URL — where Claude Code runs

The dispatcher binds `0.0.0.0:7355` by default, so the only thing that changes between
setups is the host in the URL:

| Where Claude Code runs                                | Dispatcher URL                                                                                                                   |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Same machine** as the dispatcher (no Docker)        | `ws://localhost:7355` (or `ws://127.0.0.1:7355`)                                                                                 |
| **Separate LAN machine** (typical multi-device setup) | `ws://laptop.local:7355` (mDNS) or `ws://192.168.x.y:7355` (static IP)                                                           |
| **Docker on the same host** as the dispatcher         | `ws://<bridge-gateway>:7355` — find with the snippet in [step 6](#6-configure-the-plugin); typically `172.17.0.1` / `172.18.0.1` |

## Troubleshooting

| Symptom                                       | Check                                                                                                                                                                                                |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-32000` on plugin start                      | Dispatcher not running, or `config.json` missing/in wrong path. Run `/voice:status` to check. Config must be at `~/.claude/plugins/data/voice-voice-channel/config.json` (not in a `voice/` subdir). |
| Plugin shows "disconnected" (close code 1006) | Nothing listening at the dispatcher URL. Check dispatcher is running (`lsof -i :7355` on host) and bound to `0.0.0.0`, not `127.0.0.1`.                                                              |
| No response to voice                          | Dispatcher running but mic not triggering. macOS: check mic permission (System Settings → Privacy → Microphone). Linux: check `systemctl --user status voice-dispatcher` and mic levels.             |
| mDNS not resolving                            | Use the laptop's LAN IP instead: `ws://192.168.x.y:7355`. Some mesh-router firmware suppresses mDNS.                                                                                                 |
| AirPods mic has poor accuracy                 | AirPods switch to Bluetooth HFP/SCO when used as a mic. Use the built-in mic for input — see dispatcher/README.md → "AirPods / Bluetooth headset note".                                              |
| No TTS heard on Linux                         | Headset likely in HFP mode (silent output). Pin it to A2DP and use the built-in mic — see dispatcher/README.md. Ensure `pw-play` is installed (`pipewire-bin`).                                      |
| Dispatcher says "token mismatch"              | Re-run `/voice:configure` with the correct token, or rotate: `voice-dispatcher config rotate-token jarvis`                                                                                           |

## Adding more agents

Each additional agent needs three steps (not a one-YAML-line operation):

1. `voice-dispatcher config add-agent <id> --triggers "..." --voice <voice.onnx>` on the laptop
2. `claude plugin install voice@voice-channel --scope local` inside that agent's container
3. Write `config.json` with the matching dispatcher URL + token (or run `/voice:configure`)

## Security

The dispatcher binds `0.0.0.0:7355` and uses a bearer token in the `hello` message.
Trust model: **home LAN is trusted** (WPA2/WPA3 WiFi, no port-forwarding, single-user setup).

Upgrade paths (not v1, see [PROTOCOL.md](PROTOCOL.md)):

- WSS with self-signed cert + fingerprint pinning
- Tailscale/WireGuard tunnel between laptop and agent PC

### Permission relay (opt-in, OFF by default)

The voice channel can relay Claude's tool-permission prompts: the dispatcher
speaks _"Bash needs permission, say yes or no followed by alpha bravo…"_ and you
answer by voice. The 5-letter request id must be spoken, so a bare "yes" from a TV
won't approve anything. Still, **the mic does not authenticate the speaker** —
enable it (`enable_permission_relay` in both config.yaml and `/voice:configure`)
only if you accept that anyone the mic can hear could approve a tool call. The
local terminal dialog is always the fallback. See dispatcher/README.md for details.

## Privacy

All STT and TTS are local — no data leaves your network. However, Silero VAD + Whisper-tiny
transcribes **every detected speech segment** before the trigger-match decides whether to forward.
Non-matching transcripts are discarded immediately and never leave the dispatcher process.
This is not the same as wake-word spotting (which would only transcribe on a model hit).

## Requirements

- **Dispatcher (laptop):** Python 3.11+, PortAudio (system audio), a mic + speakers,
  ~500 MB disk for the Whisper model + one Piper voice.
- **Plugin (where Claude Code runs):** [Bun](https://bun.sh) on `PATH`, plus network reach
  to the dispatcher (LAN or localhost).

## Protocol

See [PROTOCOL.md](PROTOCOL.md) for the full WebSocket message schema.

## License

Apache-2.0
