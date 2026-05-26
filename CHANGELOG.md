# Changelog

## [0.0.2] - 2026-05-26

### Changed

- **security: in-band cert pinning** — v2 pairing strings now include the dispatcher public cert PEM, and the plugin validates WSS using Bun's TLS `ca` path before sending the bearer token. This removes the previous two-connection fingerprint preflight and closes the token-leak TOCTOU window.
- **plugin: v2 config** — secure `wss://` configs now require `dispatcher_cert_pem` plus matching `dispatcher_cert_sha256`; legacy fingerprint-only configs fail closed with re-pair guidance.

### Upgrade Instructions

Upgrade both dispatcher and plugin, then re-pair every agent with a fresh `voicepair_...` string from `voice-dispatcher config rotate-token <id>` or `voice-dispatcher config add-agent <id> ...`.

## [0.0.1] - 2026-05-26

### Added

- **dispatcher: voice pipeline** — Silero VAD + faster-whisper STT + Piper TTS, fully local, no cloud.
- **dispatcher: WebSocket server** — multi-agent routing over `wss://:7355` with bearer-token auth and TLS.
- **dispatcher: trigger matching** — Levenshtein-tolerant keyword matching with configurable per-agent triggers.
- **dispatcher: permission relay** — opt-in voice approval of Claude Code tool-permission prompts.
- **plugin: MCP channel server** — TypeScript/Bun stdio server bridging the dispatcher WebSocket to Claude Code notifications.
- **plugin: `/voice:configure` and `/voice:status` skills** — manage plugin config and inspect connection state.

### Files affected

| File | Change |
|------|--------|
| `dispatcher/` | Full Python dispatcher service |
| `plugin/voice/` | Full TypeScript/Bun MCP channel plugin |
| `plugin/voice/.claude-plugin/plugin.json` | Initial manifest at 0.0.1 |
| `.claude-plugin/marketplace.json` | Initial marketplace entry at 0.0.1 |
| `PROTOCOL.md` | WebSocket wire protocol specification v1 |
| `README.md` | Setup, architecture, and usage docs; version badge |

### Upgrade Instructions

Run `/voice:configure` after installing the plugin to set your dispatcher host and bearer token.

No previous version to migrate from.
