#!/usr/bin/env node
/**
 * Voice channel plugin — MCP ↔ WebSocket bridge.
 *
 * Dispatcher runs on the operator's laptop (LAN-reachable); this process
 * runs inside an agent Docker container. The two communicate over a single
 * long-lived WebSocket. Audio never enters this process.
 */

// We use the low-level `Server`, not the high-level `McpServer`. The SDK marks
// `Server` @deprecated (TS6385) and steers casual users to `McpServer`, but its
// own note says "only use Server for advanced use cases" — channels are exactly
// that. McpServer exposes only tool()/resource()/prompt(); it has no
// setNotificationHandler and no arbitrary notification(), which the channel
// contract requires (notifications/claude/channel[/permission]). Every official
// reference channel uses `Server` too. Do not migrate.
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js'
import { z } from 'zod'
import WS from 'ws'
import { readFileSync, writeFileSync, mkdirSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'

// ── Config ────────────────────────────────────────────────────────────────────

const DATA_DIR =
  process.env.CLAUDE_PLUGIN_DATA ?? join(homedir(), '.claude', 'channels', 'voice')

const CONFIG_FILE = join(DATA_DIR, 'config.json')
const STATUS_FILE = join(DATA_DIR, 'status.json')

const ConfigSchema = z.object({
  dispatcher_url: z.string().min(1),
  token: z.string().min(1),
  agent_id: z.string().min(1).default('agent'),
  enable_permission_relay: z.boolean().default(false),
})
type Config = z.infer<typeof ConfigSchema>

function loadConfig(): Config {
  try {
    return ConfigSchema.parse(JSON.parse(readFileSync(CONFIG_FILE, 'utf8')))
  } catch (err) {
    const missing = err instanceof Error && (err as NodeJS.ErrnoException).code === 'ENOENT'
    process.stderr.write(
      missing
        ? `voice: no config at ${CONFIG_FILE}\n  run /voice:configure to set dispatcher URL and token\n`
        : `voice: invalid config at ${CONFIG_FILE}: ${err}\n`,
    )
    process.exit(1)
  }
}

const cfg = loadConfig()

// ── Status ────────────────────────────────────────────────────────────────────

function writeStatus(fields: Record<string, unknown>) {
  try {
    mkdirSync(DATA_DIR, { recursive: true })
    writeFileSync(
      STATUS_FILE,
      JSON.stringify({ ...fields, ts: new Date().toISOString() }, null, 2),
    )
  } catch { /* non-fatal */ }
}

// ── MCP server ────────────────────────────────────────────────────────────────

const experimental: Record<string, object> = { 'claude/channel': {} }
if (cfg.enable_permission_relay) experimental['claude/channel/permission'] = {}

const mcp = new Server(
  { name: 'voice', version: '0.1.0' },
  {
    capabilities: { tools: {}, experimental },
    instructions: [
      'You are receiving voice commands from the operator via the voice channel.',
      'Messages arrive as <channel source="voice"> with the transcribed spoken text.',
      "Respond with the reply tool, passing the utterance_id from the notification's meta and the text to speak aloud.",
      'Keep replies concise — they are synthesised to speech by Piper TTS.',
    ].join('\n'),
  },
)

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'reply',
      description:
        "Speak a response back through the voice channel. Text is synthesised aloud on the operator's device. Keep it concise.",
      inputSchema: {
        type: 'object',
        required: ['utterance_id', 'text'],
        properties: {
          utterance_id: {
            type: 'string',
            description: "The utterance_id from the inbound channel notification's meta block.",
          },
          text: { type: 'string', description: 'Text to speak aloud.' },
        },
      },
    },
  ],
}))

mcp.setRequestHandler(CallToolRequestSchema, async req => {
  if (req.params.name !== 'reply') {
    return {
      content: [{ type: 'text', text: `unknown tool: ${req.params.name}` }],
      isError: true,
    }
  }
  const args = req.params.arguments as { utterance_id: string; text: string }
  if (!socket || socket.readyState !== WS.OPEN) {
    return {
      content: [{ type: 'text', text: 'voice: not connected to dispatcher' }],
      isError: true,
    }
  }
  socket.send(
    JSON.stringify({ type: 'speak', utterance_id: args.utterance_id, text: args.text }),
  )
  return { content: [{ type: 'text', text: 'sent' }] }
})

// Inbound permission_request from Claude Code → relay to dispatcher (opt-in)
if (cfg.enable_permission_relay) {
  mcp.setNotificationHandler(
    z.object({
      method: z.literal('notifications/claude/channel/permission_request'),
      params: z.object({
        request_id: z.string(),
        tool_name: z.string(),
        description: z.string(),
        input_preview: z.string(),
      }),
    }),
    async ({ params }) => {
      if (!socket || socket.readyState !== WS.OPEN) return
      socket.send(JSON.stringify({ type: 'permission_request', ...params }))
    },
  )
}

// ── WebSocket client ──────────────────────────────────────────────────────────

let socket: WS | null = null
let shuttingDown = false
let reconnectAttempt = 0
let pingTimer: ReturnType<typeof setInterval> | null = null

function connect(): void {
  if (shuttingDown) return

  const ws = new WS(cfg.dispatcher_url)
  socket = ws

  ws.on('open', () => {
    reconnectAttempt = 0
    writeStatus({ state: 'connected', dispatcher_url: cfg.dispatcher_url })
    ws.send(JSON.stringify({ v: 1, type: 'hello', agent_id: cfg.agent_id, token: cfg.token }))

    if (pingTimer) clearInterval(pingTimer)
    pingTimer = setInterval(() => {
      if (ws.readyState === WS.OPEN) ws.send(JSON.stringify({ type: 'ping' }))
      else clearInterval(pingTimer!)
    }, 20_000)
    pingTimer.unref?.()
  })

  ws.on('message', raw => {
    let msg: Record<string, unknown>
    try { msg = JSON.parse(String(raw)) } catch { return }

    switch (msg.type) {
      case 'transcript': {
        writeStatus({
          state: 'connected',
          dispatcher_url: cfg.dispatcher_url,
          last_utterance_id: msg.utterance_id,
        })
        // meta must be Record<string,string> — coerce values, drop null/undefined
        // (e.g. lang is null when the dispatcher auto-detects language).
        const meta: Record<string, string> = {}
        for (const [k, v] of Object.entries({
          utterance_id: msg.utterance_id,
          trigger: msg.trigger,
          lang: msg.lang,
          ts: msg.ts,
        })) {
          if (v != null) meta[k] = String(v)
        }
        mcp.notification({
          method: 'notifications/claude/channel',
          params: { content: msg.text as string, meta },
        }).catch(err => {
          process.stderr.write(`voice: failed to deliver transcript to Claude: ${err}\n`)
        })
        break
      }

      case 'spoke':
        writeStatus({
          state: 'connected',
          dispatcher_url: cfg.dispatcher_url,
          last_spoke_utterance_id: msg.utterance_id,
        })
        break

      case 'permission_verdict':
        if (cfg.enable_permission_relay) {
          mcp.notification({
            method: 'notifications/claude/channel/permission',
            params: { request_id: msg.request_id, behavior: msg.behavior },
          }).catch(() => {})
        }
        break

      case 'pong':
        break

      default:
        process.stderr.write(`voice: unknown dispatcher message type: ${String(msg.type)}\n`)
    }
  })

  ws.on('close', (code, reason) => {
    socket = null
    if (pingTimer) { clearInterval(pingTimer); pingTimer = null }
    writeStatus({
      state: 'disconnected',
      dispatcher_url: cfg.dispatcher_url,
      last_close_code: code,
      last_close_reason: reason.toString(),
    })
    if (!shuttingDown) {
      reconnectAttempt++
      const delay = Math.min(1_000 * reconnectAttempt, 30_000)
      process.stderr.write(`voice: disconnected — reconnecting in ${delay / 1_000}s\n`)
      setTimeout(connect, delay)
    }
  })

  ws.on('error', err => {
    process.stderr.write(`voice: WebSocket error: ${err.message}\n`)
  })
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

await mcp.connect(new StdioServerTransport())

writeStatus({ state: 'connecting', dispatcher_url: cfg.dispatcher_url })
connect()

function shutdown(): void {
  if (shuttingDown) return
  shuttingDown = true
  if (pingTimer) { clearInterval(pingTimer); pingTimer = null }
  if (socket) { socket.close(); socket = null }
  writeStatus({ state: 'disconnected', reason: 'shutdown' })
  setTimeout(() => process.exit(0), 500)
}

process.stdin.on('end', shutdown)
process.stdin.on('close', shutdown)
process.on('SIGTERM', shutdown)
process.on('SIGINT', shutdown)
process.on('SIGHUP', shutdown)

process.on('unhandledRejection', err => {
  process.stderr.write(`voice: unhandled rejection: ${err}\n`)
})
process.on('uncaughtException', err => {
  process.stderr.write(`voice: uncaught exception: ${err}\n`)
})

// Orphan watchdog — mirrors Telegram plugin pattern.
// stdin events above don't fire reliably when the parent chain is severed.
const bootPpid = process.ppid
setInterval(() => {
  const orphaned =
    (process.platform !== 'win32' && process.ppid !== bootPpid) ||
    process.stdin.destroyed ||
    process.stdin.readableEnded
  if (orphaned) shutdown()
}, 5_000).unref()
