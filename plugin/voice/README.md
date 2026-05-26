# voice-channel plugin

A Claude Code **[channel](https://code.claude.com/docs/en/channels)** — an MCP ↔ WebSocket
bridge for the voice-dispatcher service. Channels are Claude Code's official way to push
messages into a running session and reply back through the same path; this is the voice
counterpart to the official
[Telegram](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/telegram),
[Discord](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/discord),
and [iMessage](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/imessage)
channels.

Runs inside each agent container. Receives transcribed voice commands from the dispatcher
on the operator's laptop, delivers them to Claude as channel notifications, and sends Claude's
replies back to be spoken aloud.

## Install

Inside the Claude Code container:

```bash
claude plugin marketplace add gtapps/voice-channel   # once
claude plugin install voice@voice-channel --scope local
```

Start a session with the channel loaded (`voice-channel` is a community plugin so the flag is required):

```bash
claude --dangerously-load-development-channels plugin:voice@voice-channel
```

Then inside the session, configure (writes `~/.claude/channels/voice/config.json` and `.env`):

```
/voice:configure
```

After configuring, restart Claude Code with the same channel flag so the MCP server starts with the new config.

## Requirements

- The [voice-dispatcher](../../dispatcher/README.md) service must be running on your laptop
- The dispatcher must have this agent registered (`voice-dispatcher config add-agent`)
- No audio hardware or Python dependencies are needed inside the container

## Skills

| Skill | Description |
|---|---|
| `/voice:configure` | Set dispatcher URL and pairing string (bundles agent ID, token, and pinned TLS cert); opt-in permission relay |
| `/voice:status` | Show connection state, TLS, last utterance, and any errors (incl. cert pin failures) |

## How it works

```
Claude Code session
    ↕ stdio MCP
voice-channel plugin (server.ts)
    ↕ WebSocket  wss://127.0.0.1:7355  (TLS + cert-pinned)
              or wss://192.168.x.y:7355  (remote / Docker)
voice-dispatcher (Python, on laptop)
    ↕ mic / speaker
Operator
```

1. Dispatcher transcribes speech and sends a `transcript` frame to this plugin
2. Plugin emits `notifications/claude/channel` → Claude sees `<channel source="voice">`
3. Claude calls the `reply` tool with `utterance_id` and response text
4. Plugin sends a `speak` frame to the dispatcher
5. Dispatcher synthesises the text with Piper TTS and plays it aloud

## Tools exposed to Claude

| Tool | Purpose |
|---|---|
| `reply` | Speak a response. Takes `utterance_id` (from the inbound `<channel source="voice">` notification's `meta`) and `text`; the dispatcher synthesises it with Piper TTS. |

## Configuration

State lives at `~/.claude/channels/voice/`. Override with `VOICE_STATE_DIR`:

```json
{
  "env": {
    "VOICE_STATE_DIR": "/data/claude/channels/voice"
  }
}
```

Claude Code injects `env` into every MCP server it spawns, so the plugin and both skills see it automatically.

**`config.json`** (public config):
```json
{
  "dispatcher_url": "wss://127.0.0.1:7355",
  "agent_id": "jarvis",
  "dispatcher_cert_sha256": "AB:12:CD:...",
  "dispatcher_cert_pem": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n",
  "enable_permission_relay": false
}
```

**`.env`** (credential, `chmod 600`):
```
VOICE_DISPATCHER_TOKEN=your-token-here
```

Run `/voice:configure` with the `voicepair_...` string from `voice-dispatcher config add-agent` to write both files interactively.

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

## Permission relay

Off by default. When enabled, tool-permission dialogs are spoken aloud and the operator can answer
by voice. Enable only on a trusted setup — see [Security → Permission relay](../../README.md#permission-relay-opt-in-off-by-default) in the root README for the full risk model.

## Uninstall

```bash
claude plugin uninstall voice@voice-channel
rm -rf ~/.claude/channels/voice/
```

To also remove the dispatcher and downloaded models, see [Uninstall](../../README.md#uninstall) in the root README.
