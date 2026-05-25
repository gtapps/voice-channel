---
name: voice:status
description: Report the current state of the voice channel connection, last utterance, and any errors.
allowed-tools:
  - Read
  - Bash(echo *)
---

# /voice:status

Report the current state of the voice channel connection.

## Resolve the state dir

First run:

```bash
echo "${VOICE_STATE_DIR:-$HOME/.claude/channels/voice}"
```

Use the output as `<STATE_DIR>` for all file paths below.

## What you do

Read `<STATE_DIR>/status.json` and `<STATE_DIR>/config.json` and report:

1. **Connection state** — `connecting`, `connected`, `disconnected`, or `error` (from `status.json`)
2. **Dispatcher URL** — from `config.json`, or "not configured" if no config exists
3. **Agent ID** — from `config.json`
4. **TLS** — if `dispatcher_url` starts with `wss://`, show "enabled (pinned)"; if `ws://`, show "disabled (plaintext)"
5. **Permission relay** — enabled or disabled
6. **Last activity** — `last_utterance_id` and `ts` from `status.json` if present
7. **Last error** — `last_close_code`, `last_close_reason`, or `last_error` if present

## Error states

### Certificate pin failure

If `status.json` has `state: 'error'` and `last_error` contains "cert pin mismatch" or "pin",
render it prominently as:

```
⚠ Certificate pin failure — token was NOT sent to the dispatcher.
  The dispatcher's TLS certificate does not match the pinned fingerprint.
  Fix: re-run /voice:configure with the correct pairing string from the dispatcher.
       (Run 'voice-dispatcher tls fingerprint' on the laptop to get the current fingerprint,
        or 'voice-dispatcher config rotate-token <id>' to generate a new pairing string.)
```

Do NOT treat this as a generic disconnect. It means the plugin detected an
impersonation attempt or stale fingerprint and refused to send the bearer token.

### Generic disconnect

If `state` is `disconnected` or `error` without a pin-related `last_error`, show
the close code, reason, and last error normally.

## If status.json does not exist

The MCP server has not started yet. Tell the user to:
1. Ensure `/voice:configure` has been run
2. Restart the Claude Code session (the MCP server starts automatically from `.mcp.json`)

## If config.json does not exist

The plugin is not configured. Tell the user to run `/voice:configure`.

## Example output — connected

```
Voice channel status
────────────────────
State:            connected
Dispatcher:       wss://192.168.1.50:7355
Agent ID:         jarvis
TLS:              enabled (pinned)
Permission relay: disabled
Last utterance:   u-1748012345 (2026-05-24T10:30:00Z)
```

## Example output — pin failure

```
Voice channel status
────────────────────
State:            error
Dispatcher:       wss://192.168.1.50:7355

⚠ Certificate pin failure — token was NOT sent to the dispatcher.
  The dispatcher's TLS certificate does not match the pinned fingerprint.
  Fix: re-run /voice:configure with the correct pairing string from the dispatcher.
```
