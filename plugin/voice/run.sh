#!/usr/bin/env bash
# Launcher for local development (used in a dev .mcp.json).
# Explicitly sets CLAUDE_PLUGIN_ROOT/DATA so they aren't overridden by the outer
# Claude Code session's environment (which may already have CLAUDE_PLUGIN_DATA
# set to its own active plugin's data directory), then runs the server via bun —
# the same runtime as the packaged plugin. Deps install on first run.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_PLUGIN_ROOT="$SCRIPT_DIR"
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA_OVERRIDE:-$HOME/.claude/channels/voice}"
cd "$SCRIPT_DIR"
bun install --no-summary
exec bun server.ts
