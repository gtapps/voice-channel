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

Then configure (writes `${CLAUDE_PLUGIN_DATA}/config.json`):

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

## Configuration

Config lives at `${CLAUDE_PLUGIN_DATA}/config.json`:

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

Off by default. When enabled, Claude's tool-permission dialogs are also spoken aloud and the
operator can answer by voice ("yes abcde" / "no abcde"). The room mic does not authenticate the
speaker — enable only on a trusted setup. See SKILL.md for the full risk disclosure.

## Runtime

**Bun** — same as the official Telegram, Discord, and iMessage channels. Bun transpiles TypeScript natively
(no tsx/esbuild build step) and its `node_modules` are pure JS, so the plugin is portable across
OS/arch. The `start` script (`bun install --production --no-summary && bun server.ts`) installs
deps on first launch; subsequent starts skip install. Bun must be on `PATH` — hermit containers
include it; otherwise install from https://bun.sh.

## Development

```bash
# Install deps (creates/updates bun.lock)
bun install

# Run tests
bun run test
```
