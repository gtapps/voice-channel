#!/bin/sh
set -e

PLUGIN_DATA="${CLAUDE_PLUGIN_DATA:?CLAUDE_PLUGIN_DATA is not set}"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:?CLAUDE_PLUGIN_ROOT is not set}"

STAMP="$PLUGIN_DATA/.package.json.stamp"

# Install deps when node_modules are missing or package.json has changed.
# npm install --prefix installs into $PLUGIN_DATA/node_modules;
# passing $PLUGIN_ROOT installs that local package + its deps.
if [ ! -d "$PLUGIN_DATA/node_modules/@modelcontextprotocol" ] || \
   ! cmp -s "$PLUGIN_ROOT/package.json" "$STAMP" 2>/dev/null; then
  echo "voice: installing dependencies into $PLUGIN_DATA..." >&2
  npm install --omit=dev --no-package-lock --prefix "$PLUGIN_DATA" "$PLUGIN_ROOT" >&2
  cp "$PLUGIN_ROOT/package.json" "$STAMP"
fi

# CWD must be PLUGIN_DATA so that `node --import tsx` resolves tsx from
# node_modules here. NODE_PATH provides a belt-and-suspenders fallback.
export NODE_PATH="$PLUGIN_DATA/node_modules"
cd "$PLUGIN_DATA"
exec node --import tsx "$PLUGIN_ROOT/server.ts"
