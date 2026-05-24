# voice-channel plugin

Claude Code channel plugin — MCP ↔ WebSocket bridge for the voice-dispatcher service.

Runs inside each hermit container. Receives transcribed voice commands from the dispatcher
on the operator's laptop, delivers them to Claude as channel notifications, and sends Claude's
replies back to be spoken aloud.

## Install

```
/plugin install voice-channel
```

Then configure:

```
/voice:configure
```

## Requirements

- The [voice-dispatcher](../../dispatcher/README.md) service must be running on your laptop
- The dispatcher must have this hermit registered (`voice-dispatcher config add-hermit`)
- No audio hardware or Python dependencies are needed inside the container

## Skills

| Skill | Description |
|---|---|
| `/voice:configure` | Set dispatcher URL, token, hermit ID, and permission-relay opt-in |
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
  "hermit_id": "jarvis",
  "enable_permission_relay": false
}
```

Run `/voice:configure` to write this file interactively.

## Permission relay

Off by default. When enabled, Claude's tool-permission dialogs are also spoken aloud and the
operator can answer by voice ("yes abcde" / "no abcde"). The room mic does not authenticate the
speaker — enable only on a trusted setup. See SKILL.md for the full risk disclosure.

## Runtime

Default: **Node + tsx**. Node is already present in any Claude Code container; `tsx` is installed
into `${CLAUDE_PLUGIN_DATA}/node_modules` by `bootstrap.sh` on first run.

Bun can be used as a development runtime: `bun server.ts` from the plugin directory.

## Development

```bash
# Install deps
npm install

# Run tests
npm test

# Lint / type-check
npx tsc --noEmit
```
