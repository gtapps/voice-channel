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

Then configure (writes `~/.claude/channels/voice/config.json`):

```
/voice:configure
```

Start a session with the channel loaded:

```bash
claude --dangerously-load-development-channels plugin:voice@voice-channel
```

## Requirements

- The [voice-dispatcher](../../dispatcher/README.md) service must be running on your laptop
- The dispatcher must have this agent registered (`voice-dispatcher config add-agent`)
- No audio hardware or Python dependencies are needed inside the container

## Skills

| Skill | Description |
|---|---|
| `/voice:configure` | Set dispatcher URL, token, agent ID, and permission-relay opt-in |
| `/voice:status` | Show connection state, last utterance, and any errors |

## How it works

```
Claude Code session
    ↕ stdio MCP
voice-channel plugin (server.ts)
    ↕ WebSocket  ws://127.0.0.1:7355  (or ws://laptop.local:7355 for remote/Docker)
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

Config lives at `~/.claude/channels/voice/config.json`. To override, set `VOICE_STATE_DIR` in
the project's `.claude/settings.local.json` or `.claude/settings.json`:

```json
{
  "env": {
    "VOICE_STATE_DIR": "/data/claude/channels/voice"
  }
}
```

Claude Code injects `env` into every MCP server it spawns, so the plugin and both skills see it automatically.

```json
{
  "dispatcher_url": "ws://127.0.0.1:7355",
  "token": "your-token-here",
  "agent_id": "jarvis",
  "enable_permission_relay": false
}
```

Run `/voice:configure` to write this file interactively.

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
