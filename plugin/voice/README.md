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
    ↕ WebSocket  ws://laptop.local:7355
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

Config lives at `~/.claude/channels/voice/config.json` (overridable via `VOICE_STATE_DIR`):

```json
{
  "dispatcher_url": "ws://laptop.local:7355",
  "token": "your-token-here",
  "agent_id": "jarvis",
  "enable_permission_relay": false
}
```

Run `/voice:configure` to write this file interactively.

## Permission relay

Off by default. When enabled, tool-permission dialogs are spoken aloud and the operator can answer
by voice. Enable only on a trusted setup — see [Security → Permission relay](../../README.md#permission-relay-opt-in-off-by-default) in the root README for the full risk model.

## Uninstall

```bash
claude plugin uninstall voice@voice-channel
rm -rf ~/.claude/channels/voice/
```

To also remove the dispatcher and downloaded models, see [Uninstall](../../README.md#uninstall) in the root README.
