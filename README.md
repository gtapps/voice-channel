# voice-channel

Ambient voice control for **any Claude Code instance**. Speak to your agents from
anywhere in the room. Originally built for
[claude-code-hermit](https://github.com/gtapps/claude-code-hermit) — long-running
agents in Docker — but the plugin is a generic channel and works in any Claude Code
session (containerized or not).

Throughout these docs, **"agent"** means a Claude Code session you talk to by voice;
each one is registered with the dispatcher under an `agent_id` and its own trigger
phrases.

## Architecture

Two components, one protocol:

```
LAPTOP (macOS or Linux — operator's device, has AirPods / built-in mic)
  voice-dispatcher (Python)
    ├── Silero VAD + faster-whisper-tiny  — local STT, no cloud
    ├── Piper TTS                          — local TTS, no cloud
    └── WebSocket server :7355 (LAN / 0.0.0.0)

          ↕  ws://laptop.local:7355  (home LAN)
             or ws://<docker-bridge-gateway>:7355  (same host)

TARGET MACHINE (where Claude Code runs — Linux + Docker, or the same laptop)
  Claude Code session
    └── voice-channel plugin (TypeScript / Node)
          └── MCP channel server ↔ dispatcher WebSocket
```

The dispatcher owns all audio; the plugin is a thin (~200 LOC) WebSocket ↔ MCP bridge.
No audio libraries or Python code enter the Claude Code environment.

## Install order

### 1. Install the dispatcher on your laptop

```bash
cd dispatcher
./install-macos.sh      # macOS (critical-path)
# or: ./install-linux.sh   (Linux laptop, acceptance-gated)
```

The installer starts the dispatcher as a service (launchd / systemd `--user`)
with an empty config — you'll restart it in step 4 once an agent is registered.

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
  --triggers "hey jarvis,agent,ó agent" \
  --voice en_US-lessac-medium.onnx
```

The command prints the token. Copy it — you'll need it in step 6.

### 4. Restart the dispatcher to load the new agent

```bash
# macOS:
launchctl kickstart -k gui/$(id -u)/com.gtapps.voice-dispatcher
# Linux:
systemctl --user restart voice-dispatcher
# Or run in the foreground for testing (logs to your terminal):
#   cd dispatcher && python -m voice_dispatcher run
```

Confirm it's listening: `lsof -i :7355` should show a `python` process on `0.0.0.0:7355`.

### 5. Install the plugin in the agent container

Run these commands **inside the container** (e.g. via `docker exec -it <container> bash`):

```bash
claude plugin marketplace add gtapps/voice-channel
claude plugin install voice@voice-channel --scope local
```

### 6. Configure the plugin

Find the Docker bridge gateway IP (how the container reaches the host):

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

Then write the config (replace values as needed):

```bash
# Inside the container — adjust DATA_DIR to match your plugin scope:
DATA_DIR="$HOME/.claude/plugins/data/voice-voice-channel"
mkdir -p "$DATA_DIR"
cat > "$DATA_DIR/config.json" <<EOF
{
  "dispatcher_url": "ws://<bridge-ip>:7355",
  "token": "<token from step 3>",
  "agent_id": "jarvis",
  "enable_permission_relay": false
}
EOF
```

Or run `/voice:configure` inside a Claude Code session — it will prompt for each value.

> **Note on `CLAUDE_PLUGIN_DATA`:** Claude Code sets this to
> `~/.claude/plugins/data/voice-voice-channel/` (without a trailing plugin-name subdir).
> `config.json` and `status.json` must live directly in that directory.

### 7. Start a Claude Code session with the voice channel

```bash
# Inside the container:
cd /path/to/your/project
claude --dangerously-load-development-channels plugin:voice@voice-channel
```

> This flag is required for community (non-Anthropic-signed) channel plugins.
> The dispatcher must be running on your laptop before Claude starts.

> **First launch:** `bootstrap.sh` runs `npm install` the first time the MCP
> server starts (~10s). If Claude Code reports `-32000` on the very first
> attempt, the install was still finishing — `/plugin` → reconnect, or restart
> the session, and it connects. Subsequent launches are instant.

### 8. Test

Say "hey jarvis, what time is it?" — Claude should reply aloud.

## Dispatcher URL — same host vs separate LAN host

| Setup | URL to use |
|---|---|
| Dispatcher and agent on the **same machine** (agent in Docker) | `ws://<bridge-gateway>:7355` — find with the Python snippet above; typically `172.17.0.1` or `172.18.0.1` |
| Dispatcher on a **separate LAN laptop** (typical setup) | `ws://laptop.local:7355` (mDNS) or `ws://192.168.x.y:7355` (static IP) |

The dispatcher binds `0.0.0.0:7355` by default so both cases work without config changes.

## Troubleshooting

| Symptom | Check |
|---|---|
| `-32000` on plugin start | Dispatcher not running, or `config.json` missing/in wrong path. Run `/voice:status` to check. Config must be at `~/.claude/plugins/data/voice-voice-channel/config.json` (not in a `voice/` subdir). |
| Plugin shows "disconnected" (close code 1006) | Nothing listening at the dispatcher URL. Check dispatcher is running (`lsof -i :7355` on host) and bound to `0.0.0.0`, not `127.0.0.1`. |
| No response to voice | Dispatcher running but mic not triggering. macOS: check mic permission (System Settings → Privacy → Microphone). Linux: check `systemctl --user status voice-dispatcher` and mic levels. |
| mDNS not resolving | Use the laptop's LAN IP instead: `ws://192.168.x.y:7355`. Some mesh-router firmware suppresses mDNS. |
| AirPods mic has poor accuracy | AirPods switch to Bluetooth HFP/SCO when used as a mic. Use the built-in mic for input — see dispatcher/README.md → "AirPods / Bluetooth headset note". |
| No TTS heard on Linux | Headset likely in HFP mode (silent output). Pin it to A2DP and use the built-in mic — see dispatcher/README.md. Ensure `pw-play` is installed (`pipewire-bin`). |
| Dispatcher says "token mismatch" | Re-run `/voice:configure` with the correct token, or rotate: `voice-dispatcher config rotate-token jarvis` |

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
speaks *"Bash needs permission, say yes or no followed by alpha bravo…"* and you
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

| Component | Platform | Status |
|---|---|---|
| voice-dispatcher | macOS | v1 critical-path |
| voice-dispatcher | Linux laptop | v1 acceptance-gated |
| voice-dispatcher | Windows laptop | v1 acceptance-gated |
| voice-channel plugin | Linux + Docker | v1 critical-path |

## Protocol

See [PROTOCOL.md](PROTOCOL.md) for the full WebSocket message schema.

## License

Apache-2.0
