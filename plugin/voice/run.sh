#!/usr/bin/env bash
# Launcher for local development (used in .mcp.json).
# Explicitly sets CLAUDE_PLUGIN_DATA so it isn't overridden by the outer
# Claude Code session's environment (which may already have CLAUDE_PLUGIN_DATA
# set to its own active plugin's data directory).
#
# Prereq: node_modules must be present — run `npm install` in this directory
# once before using this script. bootstrap.sh handles this automatically for
# the real in-container install path.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_PLUGIN_ROOT="$SCRIPT_DIR"
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA_OVERRIDE:-$HOME/.claude/channels/voice}"
cd "$SCRIPT_DIR"
exec node --import tsx "$SCRIPT_DIR/server.ts"
