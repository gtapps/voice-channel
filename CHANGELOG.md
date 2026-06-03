# Changelog

## [0.0.4] - 2026-06-03

This release significantly improves **wake-word detection reliability**: a pre-roll buffer prevents clipping the start of utterances, a sliding-window fuzzy matcher handles filler words and per-word edit tolerance, Whisper is biased toward your configured trigger vocabulary, and loudness is normalized before transcription to help quiet Bluetooth/HFP captures.

### Changed

- **dispatcher: trigger matching — sliding window + per-word tolerance** — the matcher now tries up to three starting offsets so filler words prepended by Whisper ("um hey jarvis") don't prevent a match. Tolerance is now one allowed edit per trigger word (previously a character-count heuristic), making it more predictable across short and long triggers.
- **dispatcher: default Whisper model `tiny` → `base`** — better transcription accuracy out of the box at a modest CPU cost. Override with `whisper.model: tiny` in `config.yaml` if needed on low-power hardware.

### Added

- **dispatcher: pre-roll audio buffer** — 320 ms of audio is buffered before VAD onset so the first syllable of an utterance is never clipped before Whisper sees it.
- **dispatcher: Whisper initial-prompt biasing** — the configured trigger phrases are fed to Whisper as an `initial_prompt`, nudging the model toward your vocabulary and reducing mis-transcriptions of trigger words.
- **dispatcher: `normalize_gain` config option** — loudness-normalizes captured audio before transcription (default `true`). Particularly effective for AirPods and other Bluetooth sources that capture at low gain. Disable with `audio.normalize_gain: false` in `config.yaml`.
- **dispatcher: `trigger_tolerance` config option** — override the per-word edit tolerance globally (`audio.trigger_tolerance: N` in `config.yaml`). Leave unset to use the automatic one-edit-per-word default.

### Upgrade Instructions

Upgrade dispatcher only — no plugin changes in this release. No re-pairing required. The default Whisper model changes from `tiny` to `base`; if transcription feels slower on your hardware, add `whisper: {model: tiny}` to `config.yaml` to revert.

## [0.0.3] - 2026-05-27

This release brings full **macOS compatibility**: the entire setup flow — pairing, TLS cert pinning, and project-local state isolation — now works on macOS without manual workarounds. Both platforms also gain a new trigger-beep feature.

### Changed

- **compatibility: macOS support** — `voice-dispatcher` and `/voice:configure` are now fully tested and supported on macOS; pairing, cert-pinning, and state-isolation all work out of the box.

### Added

- **dispatcher: trigger beep** — plays the OS notification sound immediately when a trigger phrase is matched, giving the operator an instant "got it" cue before TTS begins. Uses `afplay` on macOS, `pw-play`/`paplay` on Linux, and a synthesized 880 Hz tone via sounddevice as a universal fallback. Enabled by default; disable with `notifications.trigger_beep: false` in `config.yaml`.

### Fixed

- **plugin: configure macOS PEM fix** — decode and verify the pairing string in a single `bun` command so the cert check never hand-parses the PEM, eliminating the spurious "pem has lines of non-standard length" error on macOS.
- **plugin: project-local state dir** — `/voice:configure` now pins `VOICE_STATE_DIR` to `<project>/.claude/channels/voice` in the project's `settings.local.json`, automatically isolating each project's token and gitignoring the state dir so the token can't be committed by accident.
- **plugin: error hardening** — a malformed pairing-string paste now emits a clean `ERROR` line (no stack trace); the status skill and README explain the "works but shows not configured" case for installs that predate project-local state dirs.

### Upgrade Instructions

Upgrade both dispatcher and plugin together. No re-pairing is required.

To gain per-project token isolation, re-run `/voice:configure` in each project — it will write a fresh `settings.local.json` entry pointing to a project-local state dir. Existing setups that skip this continue to work from the old global state dir.

## [0.0.2] - 2026-05-26

### Changed

- **security: in-band cert pinning** — v2 pairing strings now include the dispatcher public cert PEM, and the plugin validates WSS using Bun's TLS `ca` path before sending the bearer token. This removes the previous two-connection fingerprint preflight and closes the token-leak TOCTOU window.
- **plugin: v2 config** — secure `wss://` configs now require `dispatcher_cert_pem` plus matching `dispatcher_cert_sha256`; legacy fingerprint-only configs fail closed with re-pair guidance.
- **dispatcher: auth failure logging** — token rejection now logs the claimed agent ID so operators can diagnose misconfigured agents without enabling debug mode.
- **plugin: permanent 4001 close** — the plugin no longer retries after a `4001 Unauthorized` close; it writes a clear error with fix instructions to `status.json` and exits cleanly, preventing reconnect storms on bad tokens.

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
