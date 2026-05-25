# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code **channel plugin** for hands-free voice control: speak a trigger phrase, Claude
replies aloud. Fully local STT (Whisper) + TTS (Piper), no cloud. Two components in one repo,
joined by one WebSocket protocol — see `PROTOCOL.md` for the wire schema (v1).

- **`dispatcher/`** — Python service that runs on the operator's laptop. Owns the mic and
  speakers; does VAD + STT + trigger matching + TTS. Routes voice to/from agents over WebSocket.
- **`plugin/voice/`** — TypeScript/Bun MCP channel server that runs _where Claude Code runs_
  (same laptop, another LAN machine, or a Docker container). Bridges the dispatcher WebSocket to
  Claude Code's MCP channel notifications. **Audio never enters this process.**

The dispatcher is the WebSocket _server_ (`0.0.0.0:7355`); the plugin is the _client_. Multiple
agents (one per Claude Code instance) connect to one dispatcher, each authenticated by a bearer
token sent in the `hello` frame.

## Commands

```bash
# Dispatcher (Python) — from dispatcher/
pytest                                  # full suite
pytest tests/test_core.py::test_name    # single test
pip install -e ".[dev]"                 # editable install with pytest deps

# Plugin (TypeScript/Bun) — from plugin/voice/
bun install                             # install deps (creates/updates bun.lock)
bun run test                            # vitest, runs tests/protocol.test.ts
```

CI (`.github/workflows/ci.yml`) installs the dispatcher **lean** (`pip install --no-deps`, then a
handful of non-audio deps) because the heavy audio libs (faster-whisper, piper-tts, onnxruntime,
torch) are imported lazily — the suite runs without them. Install plugin deps first so the
dispatcher's WS integration test, which spawns `bun server.ts`, runs instead of skipping.

### Running the dispatcher locally

```bash
voice-dispatcher run                 # start everything (audio + WebSocket server)
voice-dispatcher run --no-adapter    # audio + core only, no WebSocket (standalone audio testing)
voice-dispatcher config add-agent <id> --triggers "..." --voice <voice.onnx>
voice-dispatcher config remove-agent <id>
voice-dispatcher list-devices
```

### Test environment overrides

- `VOICE_DISPATCHER_CONFIG_DIR` — relocate the dispatcher's `config.yaml` dir (default
  `~/.config/voice-dispatcher`).
- `VOICE_STATE_DIR` — relocate the plugin's state dir (default `~/.claude/channels/voice`,
  holding `config.json` + `status.json`).

## Architecture — dispatcher

The dispatcher is built around a strict **transport-neutral core**. The rule that organizes the
whole codebase: _the core never touches sockets or wire formats; adapters never contain business
logic._

- **`core/handlers.py`** — `Dispatcher` class. The four handler methods (`route_transcript`,
  `speak`, `request_permission`, `submit_permission_verdict`) are the _only_ public surface
  adapters and the audio pipeline call. They resolve `agent_id → Session`, then emit events.
- **`core/bus.py`** — `EventBus`, a synchronous in-process pub/sub. Callbacks fire on the
  emitter's thread; heavy work must be dispatched to a queue/thread inside the callback.
- **`core/models.py`** — frozen dataclasses; the only currency between pipeline, core, and
  adapters. Adding optional fields is non-breaking; adding/removing event types is the breaking
  kind.
- **`core/session.py`** — `Session` (per-agent mutable state, lock-protected) + `SessionRegistry`.
- **`adapters/websocket.py`** — the _only_ file that knows about WS frames. Verifies token + `v`,
  parses inbound frames into core handler calls, subscribes to the bus to push outbound frames.
  Bus callbacks run on arbitrary threads, so it uses `loop.call_soon_threadsafe` to schedule sends.
- **`audio/pipeline.py`** — mic → Silero VAD → faster-whisper → trigger match → `route_transcript`,
  and the reverse: subscribes to `SpeakRequest`/`PermissionRequested` events → Piper TTS → speaker.

Data flow: voice in → pipeline matches a trigger → `route_transcript` → `TranscriptDispatched`
event → WS adapter sends `transcript` frame → plugin. Reply: plugin sends `speak` frame → adapter
calls `speak()` → `SpeakRequest` event → pipeline synthesizes + plays → emits `SpeakCompleted` →
adapter sends `spoke` frame.

### Key invariants and gotchas

- **Half-duplex**: the mic stream is paused (`self._speaking` Event) while TTS plays, so the
  system never transcribes its own voice. The audio callback drops chunks while speaking.
- **Lazy audio imports**: every `sounddevice`/`silero`/`whisper`/`piper`/`torch` import is inside a
  method, guarded so the core unit tests run without them installed. Preserve this — don't hoist
  audio imports to module top level.
- **piper-tts ≥1.4**: use `voice.synthesize_wav(text, wave_file)`. Plain `synthesize()` became an
  `AudioChunk` iterable in 1.4 and writes nothing.
- **Linux audio**: TTS plays via `pw-play` (PipeWire) writing to a temp WAV, falling back to
  sounddevice. Input falls back to the `sysdefault` device (PipeWire/dmix, supports 16 kHz) when
  none is configured. See the memory note on AirPods A2DP/HFP and mic-gain gotchas.
- **Trigger matching** (`match_trigger`): tokenizes on whitespace, compares the first N transcript
  tokens against the trigger with Levenshtein distance; default tolerance scales with trigger
  length (`max(1, len // 5)`) to reject near-homophones while accepting one-off mishearings.

## Architecture — plugin

`server.ts` is a single-file MCP stdio server + WebSocket client.

- Uses the **low-level `@modelcontextprotocol/sdk` `Server`**, not `McpServer` — deliberate and
  documented at the top of the file. `McpServer` lacks `notification()`/`setNotificationHandler`,
  which the channel contract (`notifications/claude/channel[/permission]`) requires. **Do not
  migrate to `McpServer`.**
- Exposes one MCP tool, `reply(utterance_id, text)`, which sends a `speak` frame. The
  `utterance_id` correlates the reply to the originating utterance for half-duplex gating.
- WebSocket client auto-reconnects with capped linear backoff, pings every 20s, and writes
  connection state to `status.json` (read by `/voice:status`).
- An **orphan watchdog** (`setInterval` checking `ppid`/stdin) shuts the process down if its parent
  Claude Code is severed — mirrors the official Telegram channel pattern.
- Runtime is **Bun** (matches the official channels). The `start` script runs
  `bun install --production` on first launch, then `bun server.ts`. No build step.

### Slash commands (skills)

`plugin/voice/skills/configure/` and `.../status/` define `/voice:configure` and `/voice:status`,
which read/write `config.json` and `status.json` in the channel state dir.

## Permission relay (opt-in, OFF by default)

The dispatcher can speak Claude's tool-permission prompts and accept a _spoken_ verdict. It must be
enabled on **both** sides: `enable_permission_relay: true` in the dispatcher's `config.yaml` _and_
in the plugin config via `/voice:configure`. The plugin only advertises the
`claude/channel/permission` MCP capability when enabled.

Security model: the mic does not authenticate the speaker, and the operator approves by _tool name_
only (never the arguments). The spoken verdict must include the 5-letter `request_id` — grammar
`[a-km-z]{5}` (excludes `l`, matching Claude Code's ID generator) — so ambient "yes" can't approve.
NATO phonetic spelling is accepted. The local terminal dialog is always the fallback; the voice
window times out after 30s.

## Protocol versioning

`PROTOCOL.md` is the source of truth. Bump `v` only for _breaking_ changes (new required message
type, removed field). Adding optional fields to existing messages is non-breaking. Unknown message
types are silently ignored on both ends for forward-compatibility. Field is `tool_name` (not
`tool`) to match the channels-reference docs verbatim.

## Conventions

- The repo root is the marketplace (`.claude-plugin/marketplace.json`); the plugin lives at
  `plugin/voice/`. Keep `version` fields in `plugin.json`, `package.json`, and `marketplace.json`
  coherent when releasing.
