# voice-channel

Ambient voice control for [claude-code-hermit](https://github.com/gtapps/claude-code-hermit).
Speak to your hermits from anywhere in the room.

## Architecture

Two components, one protocol:

```
LAPTOP (macOS — operator's device, has AirPods / built-in mic)
  voice-dispatcher (Python)
    ├── Silero VAD + faster-whisper-tiny  — local STT, no cloud
    ├── Piper TTS                          — local TTS, no cloud
    └── WebSocket server :7355 (LAN)

          ↕  ws://laptop.local:7355  (home LAN)

HERMIT PC (Linux + Docker)
  hermit container
    └── voice-channel plugin (TypeScript / Node)
          └── MCP channel server → Claude Code session
```

The dispatcher owns all audio; the plugin is a thin (~200 LOC) WebSocket ↔ MCP bridge.
No audio libraries or Python code enter the container.

## Install order

### 1. Install the dispatcher on your laptop

```bash
cd dispatcher
./install-macos.sh      # macOS (critical-path)
# or: ./install-linux.sh   (Linux laptop, acceptance-gated)
```

### 2. Add a hermit to the dispatcher

```bash
voice-dispatcher config add-hermit jarvis \
  --triggers "hey jarvis,hermit,ó hermit" \
  --voice en_US-lessac-medium.onnx
```

The command prints the token. Copy it.

### 3. Install the plugin in the hermit container

Inside the Claude Code session running in that container:

```
/plugin install voice-channel
/voice:configure
```

Enter the dispatcher URL (`ws://laptop.local:7355` or a LAN IP), the token from step 2,
and the hermit ID (`jarvis`).

### 4. Test

Say "hey jarvis, what time is it?" — Claude should reply aloud.

## Troubleshooting

| Symptom | Check |
|---|---|
| No response to voice | `voice-dispatcher status` on the laptop; check mic permission (macOS: System Settings → Privacy → Microphone) |
| Plugin shows "disconnected" | `/voice:status` in hermit; verify `dispatcher_url` in config and that `laptop.local` resolves (`ping laptop.local`) |
| mDNS not resolving | Use the laptop's LAN IP instead: `ws://192.168.x.y:7355` |
| AirPods mic has poor accuracy | Expected — AirPods switch to Bluetooth SCO when mic is active. Use the laptop's built-in mic for input in `config.yaml.example` |
| Dispatcher says "token mismatch" | Re-run `/voice:configure` with the correct token, or rotate: `voice-dispatcher config rotate-token jarvis` |

## Adding more hermits

Each additional hermit needs three steps (not a one-YAML-line operation):

1. `voice-dispatcher config add-hermit <id> --triggers "..." --voice <voice.onnx>`
2. `/plugin install voice-channel` inside that hermit's container
3. `/voice:configure` with the matching dispatcher URL + token

## Security

The dispatcher binds `0.0.0.0:7355` and uses a bearer token in the `hello` message.
Trust model: **home LAN is trusted** (WPA2/WPA3 WiFi, no port-forwarding, single-user setup).

Upgrade paths (not v1, see [PROTOCOL.md](PROTOCOL.md)):
- WSS with self-signed cert + fingerprint pinning
- Tailscale/WireGuard tunnel between laptop and hermit PC

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
