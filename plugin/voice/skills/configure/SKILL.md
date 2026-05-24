---
name: voice:configure
description: Configure the voice channel connection — dispatcher URL, token, hermit ID, and optional permission-relay opt-in.
---

# /voice:configure

Configure the voice channel connection inside this hermit container.

## What you do

Ask the user for the following values, then write them to `${CLAUDE_PLUGIN_DATA}/config.json`:

1. **dispatcher_url** — WebSocket URL of the voice-dispatcher service on the operator's laptop.
   Default: `ws://laptop.local:7355`. If mDNS is not working on their network, they can use the
   laptop's LAN IP: `ws://192.168.x.y:7355`.

2. **token** — Bearer token printed by the dispatcher when they ran:
   `voice-dispatcher config add-hermit <id> --triggers "..." --voice <voice.onnx>`
   (The token is auto-generated and printed — copy it from that output.)
   This token is the only authentication gate — treat it like a password.

3. **hermit_id** — The identifier this hermit uses when connecting (must match what was passed to
   `add-hermit` on the dispatcher side). Default: `hermit`.

4. **enable_permission_relay** — Whether to relay Claude's tool-permission prompts through the voice
   channel. **OFF by default.** Before enabling, explain the risk:

   > The voice channel does not authenticate the speaker — anyone whose voice the mic can hear
   > (a TV, a housemate, someone in the hallway) could say "yes <id>" and approve a tool call.
   > Only enable this if you accept that risk and understand that local terminal approval is always
   > available as the fallback.

## Config file format

Write to `${CLAUDE_PLUGIN_DATA}/config.json`:

```json
{
  "dispatcher_url": "ws://laptop.local:7355",
  "token": "<token>",
  "hermit_id": "<hermit_id>",
  "enable_permission_relay": false
}
```

## After writing

Tell the user the MCP server will reconnect automatically on the next Claude Code session start,
or they can restart the current session to connect immediately. This skill only configures the
plugin inside this container — it does NOT modify the dispatcher's YAML on the laptop.

## Notes

- This skill writes only to `${CLAUDE_PLUGIN_DATA}/config.json` inside this container.
- To add this hermit to the dispatcher, the user must run on their laptop:
  `voice-dispatcher config add-hermit <hermit_id> --triggers "hey jarvis,hermit"`
- The dispatcher URL accepts both mDNS hostnames and bare IP addresses.
