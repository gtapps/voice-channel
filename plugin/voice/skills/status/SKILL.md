---
name: voice:status
description: Report the current state of the voice channel connection, last utterance, and any errors.
---

# /voice:status

Report the current state of the voice channel connection.

## What you do

Read `${CLAUDE_PLUGIN_DATA}/status.json` and `${CLAUDE_PLUGIN_DATA}/config.json` and report:

1. **Connection state** — `connecting`, `connected`, or `disconnected` (from `status.json`)
2. **Dispatcher URL** — from `config.json`, or "not configured" if no config exists
3. **Hermit ID** — from `config.json`
4. **Permission relay** — enabled or disabled
5. **Last activity** — `last_utterance_id` and `ts` from `status.json` if present
6. **Last error** — `last_close_code`, `last_close_reason`, or any error field if present

## If status.json does not exist

The MCP server has not started yet. Tell the user to:
1. Ensure `/voice:configure` has been run
2. Restart the Claude Code session (the MCP server starts automatically from `.mcp.json`)

## If config.json does not exist

The plugin is not configured. Tell the user to run `/voice:configure`.

## Example output

```
Voice channel status
────────────────────
State:            connected
Dispatcher:       ws://laptop.local:7355
Hermit ID:        jarvis
Permission relay: disabled
Last utterance:   u-1748012345 (2026-05-24T10:30:00Z)
```
