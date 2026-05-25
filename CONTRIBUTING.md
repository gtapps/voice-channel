# Contributing

## Runtime

**Bun** — same as the official Telegram, Discord, and iMessage channels. Bun transpiles TypeScript
natively (no tsx/esbuild build step) and its `node_modules` are pure JS, so the plugin is portable
across OS/arch. The `start` script (`bun install --production --no-summary && bun server.ts`)
installs deps on first launch; subsequent starts skip install. Bun must be on `PATH` — hermit
containers include it; otherwise install from https://bun.sh.

## Development

```bash
# Install deps (creates/updates bun.lock)
cd plugin/voice
bun install

# Run plugin tests
bun run test

# Run dispatcher tests
cd ../../dispatcher
pytest
```
