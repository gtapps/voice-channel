# Voice channel WebSocket protocol — v1

Single WebSocket per agent, JSON messages, explicitly versioned.

**Connection URL:** `ws://laptop.local:7355/` (mDNS) or `ws://192.168.x.y:7355/` (LAN IP fallback).
**No query string.** The token is sent only in the `hello` message.

---

## Handshake

Immediately after connecting, the agent sends:

```json
{ "v": 1, "type": "hello", "agent_id": "jarvis", "token": "..." }
```

- `v` — protocol version. The dispatcher closes the socket with a clear reason on version mismatch.
- `token` — bearer token. The dispatcher closes the socket on failure.

---

## Dispatcher → Agent: transcript

Sent when a trigger fires and STT completes:

```json
{
  "type": "transcript",
  "utterance_id": "u-1748012345-abc",
  "text": "turn on the lights",
  "lang": "en",
  "trigger": "hey jarvis",
  "ts": "2026-05-24T10:30:00.000Z"
}
```

---

## Agent → Dispatcher: speak

Sent when Claude calls the `reply` tool:

```json
{ "type": "speak", "utterance_id": "u-1748012345-abc", "text": "Lights are now on." }
```

`utterance_id` is required — it correlates the reply to the originating utterance so the dispatcher
can gate half-duplex correctly when transcripts queue.

---

## Dispatcher → Agent: spoke

Sent when TTS playback of a `speak` frame has finished:

```json
{ "type": "spoke", "utterance_id": "u-1748012345-abc" }
```

---

## Permission relay (opt-in)

Only sent when the agent declared `claude/channel/permission` in its MCP capabilities
(`enable_permission_relay: true` in the plugin config).

### Agent → Dispatcher: permission_request

```json
{
  "type": "permission_request",
  "request_id": "abcde",
  "tool_name": "Bash",
  "description": "Run a shell command",
  "input_preview": "{\"command\":\"pwd\"}"
}
```

Field name is `tool_name` (not `tool`) — matches the channels-reference docs verbatim.

### Dispatcher → Agent: permission_verdict

```json
{ "type": "permission_verdict", "request_id": "abcde", "behavior": "allow" }
```

`behavior` is `"allow"` or `"deny"`.

**`request_id` grammar:** `[a-km-z]{5}` — 5 lowercase letters excluding `l` (matches Claude Code's
actual ID generator).

---

## Keepalive

```json
{ "type": "ping" }   →   { "type": "pong" }
```

Sent every 20 seconds by the plugin. Dispatcher should respond with `pong`. Dispatcher may also
send `ping`; plugin responds with `pong`.

---

## Error handling

- Protocol version mismatch: dispatcher closes with code 4000, reason `"unsupported protocol version vN"`.
- Token failure: dispatcher closes with code 4001, reason `"authentication failed"`.
- Superseded connection: dispatcher closes the *old* socket with code 4002, reason `"superseded by new connection"`, when the same agent reconnects. The new connection takes over immediately.
- Unknown message types are silently ignored (forward-compatibility).

---

## Versioning

Bump `v` when adding a **breaking** message type or removing a field. Adding optional fields to
existing message types is non-breaking. The current version is `1`.
