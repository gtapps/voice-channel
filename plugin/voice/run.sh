#!/usr/bin/env bash
# Launcher for local development (used in .mcp.json).
# Explicitly sets CLAUDE_PLUGIN_ROOT/DATA so they aren't overridden by the outer
# Claude Code session's environment (which may already have CLAUDE_PLUGIN_DATA
# set to its own active plugin's data directory), then hands off to bootstrap.sh
# — the single source of the dependency-install + launch logic. Deps install
# automatically on first run; no manual `npm install` needed.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_PLUGIN_ROOT="$SCRIPT_DIR"
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA_OVERRIDE:-$HOME/.claude/channels/voice}"
exec "$SCRIPT_DIR/bootstrap.sh"
