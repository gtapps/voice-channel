---
name: voice:configure
description: Configure the voice channel connection — dispatcher URL, token, agent ID, and optional permission-relay opt-in.
allowed-tools:
  - Read
  - Write
  - Bash(echo *)
  - Bash(mkdir *)
---

# /voice:configure

Configure the voice channel connection inside this agent container.

## Resolve the state dir

Run:

```bash
echo "${VOICE_STATE_DIR:-$HOME/.claude/channels/voice}"
```

Use the output as `<STATE_DIR>` for every file path below.

## Detect existing config

Check if `<STATE_DIR>/config.json` exists. If it does, read it and record the current values for
`dispatcher_url`, `token`, `agent_id`, and `enable_permission_relay`. Tell the user:
*"Found existing config — showing current values as defaults."*

## Collect settings

### Call 1 — Connection + identity + permission relay

Ask all three in a single `AskUserQuestion` call:

```
questions: [
  {
    header: "Dispatcher URL",
    question: "WebSocket URL of the voice-dispatcher on your laptop?",
    options: [
      // Always show the mDNS default first.
      // If existing config differs from the default, replace option 2 with the current value.
      // If existing config matches the default (or no config exists), use the LAN IP fallback as option 2.
      { label: "ws://laptop.local:7355", description: "mDNS hostname — default" },
      { label: "<current value OR 'Use LAN IP instead'>", description: "<'Current value' OR 'Enter ws://192.168.x.y:7355 via Other if mDNS isn't working'>" }
    ]
  },
  {
    header: "Agent ID",
    question: "Identifier for this agent (must match the dispatcher's add-agent command)?",
    options: [
      // Always show the default first.
      // If existing config differs from the default, replace option 2 with the current value.
      // If existing config matches the default (or no config exists), use a descriptive second option.
      { label: "agent", description: "Default" },
      { label: "<current value OR 'Use a different ID'>", description: "<'Current value' OR 'Type a custom agent_id via Other'>" }
    ]
  },
  {
    header: "Permission relay",
    question: "Relay Claude's tool-permission prompts through the voice channel?",
    options: [
      { label: "No — keep off", description: "Terminal approval only. Safest — anyone the mic can hear could otherwise say 'yes <id>' and approve a tool call. (default)" },
      { label: "Yes — enable", description: "⚠ Voice approval is unauthenticated. Only enable if you accept that risk and understand terminal approval is always the fallback." }
    ]
  }
]
```

`AskUserQuestion` requires at least 2 options. For dispatcher_url and agent_id:
- If existing config has a value different from the default → options are [default, current value]
- If existing config matches the default, or there is no existing config → options are [default, meaningful alternative]
  (LAN IP fallback for dispatcher_url; "Use a different ID" for agent_id)

### Call 2 — Token

Ask alone so the user can focus on pasting a secret value:

```
questions: [
  {
    header: "Auth token",
    question: "Bearer token printed by the dispatcher when you ran: voice-dispatcher config add-agent <id> --triggers '...' --voice <voice.onnx>",
    options: [
      // Include ONLY if existing config has a token:
      { label: "Keep existing token", description: "Leave the current token unchanged" },
      { label: "I don't have the token yet", description: "Run the add-agent command above first — the token is auto-generated and printed. Re-run /voice:configure after." }
    ]
    // User pastes the actual token value via Other
  }
]
```

Handle the result:
- **"Keep existing token"** → keep the token from the existing config unchanged
- **"I don't have the token yet"** → stop here; remind the user to run
  `voice-dispatcher config add-agent <agent_id> --triggers "..."` on their laptop, then re-run
  `/voice:configure`
- **Other (typed value)** → use as the new token

## Write config.json

Create `<STATE_DIR>` if it does not exist, then write `<STATE_DIR>/config.json`:

```json
{
  "dispatcher_url": "<dispatcher_url>",
  "token": "<token>",
  "agent_id": "<agent_id>",
  "enable_permission_relay": <enable_permission_relay>
}
```

## After writing

Tell the user the MCP server will reconnect automatically on the next Claude Code session start,
or they can restart the current session to connect immediately. This skill only configures the
plugin inside this container — it does NOT modify the dispatcher's config on the laptop.

## Notes

- This skill writes only to `<STATE_DIR>/config.json` inside this container.
- To add this agent to the dispatcher, the user must run on their laptop:
  `voice-dispatcher config add-agent <agent_id> --triggers "hey jarvis,jarvis"`
- The dispatcher URL accepts both mDNS hostnames and bare IP addresses.
- The token is the only authentication gate — treat it like a password.
