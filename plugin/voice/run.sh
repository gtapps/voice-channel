#!/usr/bin/env bash
# Launcher for local development (used in a dev .mcp.json).
# Sets CLAUDE_PLUGIN_ROOT so it is accurate for the dev tree, then runs the
# server via bun — the same runtime as the packaged plugin. Deps install on
# first run.
#
# State dir: the server uses VOICE_STATE_DIR ?? ~/.claude/channels/voice.
# Export VOICE_STATE_DIR before running this script to redirect state for
# testing.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_PLUGIN_ROOT="$SCRIPT_DIR"
cd "$SCRIPT_DIR"
bun install --no-summary
exec bun server.ts
