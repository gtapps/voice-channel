#!/bin/sh
set -e

PLUGIN_DATA="${CLAUDE_PLUGIN_DATA:?CLAUDE_PLUGIN_DATA is not set}"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:?CLAUDE_PLUGIN_ROOT is not set}"

STAMP="$PLUGIN_DATA/.package.json.stamp"

# Install deps when node_modules are missing or package.json has changed.
# Copy package.json to PLUGIN_DATA and run npm install there so that all
# declared dependencies land in PLUGIN_DATA/node_modules.  Passing a local
# path to npm install only installs that package itself (1 pkg), not its deps.
if [ ! -d "$PLUGIN_DATA/node_modules/@modelcontextprotocol" ] || \
   ! cmp -s "$PLUGIN_ROOT/package.json" "$STAMP" 2>/dev/null; then
  echo "voice: installing dependencies into $PLUGIN_DATA..." >&2
  cp "$PLUGIN_ROOT/package.json" "$PLUGIN_DATA/package.json"
  npm install --omit=dev --no-package-lock --prefix "$PLUGIN_DATA" >&2
  cp "$PLUGIN_ROOT/package.json" "$STAMP"
fi

# CWD must be PLUGIN_DATA so that `node --import tsx` resolves tsx from
# node_modules here. NODE_PATH provides a belt-and-suspenders fallback.
export NODE_PATH="$PLUGIN_DATA/node_modules"
cd "$PLUGIN_DATA"
exec node --import tsx "$PLUGIN_ROOT/server.ts"
