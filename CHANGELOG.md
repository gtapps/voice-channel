# Changelog

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
