#!/usr/bin/env bun
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
import { createHash, X509Certificate } from 'node:crypto'
import { readFileSync, writeFileSync, mkdirSync, chmodSync, existsSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'

// ── Config ────────────────────────────────────────────────────────────────────
// State lives in ~/.claude/channels/voice/ — the Claude Code user-scope channel
// convention, siblings to discord and telegram. Override with VOICE_STATE_DIR
// for tests or a non-default install location. Never read CLAUDE_PLUGIN_DATA:
// that is a CC-managed per-plugin sandbox, not the shared channel state dir.

const STATE_DIR =
  process.env.VOICE_STATE_DIR ?? join(homedir(), '.claude', 'channels', 'voice')

const CONFIG_FILE = join(STATE_DIR, 'config.json')
const STATUS_FILE = join(STATE_DIR, 'status.json')
const ENV_FILE    = join(STATE_DIR, '.env')

// ── Helpers ───────────────────────────────────────────────────────────────────

function writeStatus(fields: Record<string, unknown>) {
  try {
    mkdirSync(STATE_DIR, { recursive: true })
    writeFileSync(
      STATUS_FILE,
      JSON.stringify({ ...fields, ts: new Date().toISOString() }, null, 2),
    )
  } catch { /* non-fatal */ }
}

function normalizePin(s: string): string {
  // Strip optional 'sha256:' prefix, colons, spaces, and lowercase → 64 hex chars
  return s.replace(/^sha256:/i, '').replace(/[:\s]/g, '').toLowerCase()
}

function fingerprintPem(certPem: string): string {
  const x509 = new X509Certificate(certPem)
  return createHash('sha256').update(x509.raw).digest('hex')
}

// ── .env loader ───────────────────────────────────────────────────────────────
// Bearer token lives in <STATE_DIR>/.env — the token is a credential.
// Mirror discord/telegram channel pattern.

function loadEnvToken(): string | undefined {
  if (existsSync(ENV_FILE)) {
    try {
      chmodSync(ENV_FILE, 0o600) // ensure it stays credential-mode
      const lines = readFileSync(ENV_FILE, 'utf8').split('\n')
      for (const line of lines) {
        const m = line.match(/^VOICE_DISPATCHER_TOKEN=(.+)$/)
        if (m) {
          const val = m[1].trim()
          if (val) return val
        }
      }
    } catch { /* non-fatal — fall through to env var */ }
  }
  return undefined
}

// ── Config schema ─────────────────────────────────────────────────────────────

const ConfigSchema = z.object({
  dispatcher_url: z.string().regex(/^wss?:\/\//, 'dispatcher_url must start with ws:// or wss://'),
  agent_id: z.string().min(1).default('agent'),
  dispatcher_cert_sha256: z.string().optional(),
  dispatcher_cert_pem: z.string().optional(),
  enable_permission_relay: z.boolean().default(false),
}).superRefine((val, ctx) => {
  if (!val.dispatcher_url.startsWith('wss://')) return

  if (!val.dispatcher_cert_sha256) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'dispatcher_cert_sha256 is required when dispatcher_url uses wss://. Re-run /voice:configure with a v2 pairing string.',
    })
  } else if (!/^[0-9a-f]{64}$/.test(normalizePin(val.dispatcher_cert_sha256))) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'dispatcher_cert_sha256 must be a 64-char hex SHA-256 fingerprint (colons and sha256: prefix are stripped automatically)',
    })
  }

  if (!val.dispatcher_cert_pem) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'dispatcher_cert_pem is required when dispatcher_url uses wss://. Re-run /voice:configure with a v2 pairing string.',
    })
    return
  }

  try {
    const actual = fingerprintPem(val.dispatcher_cert_pem)
    if (val.dispatcher_cert_sha256 && actual !== normalizePin(val.dispatcher_cert_sha256)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'dispatcher_cert_pem does not match dispatcher_cert_sha256. Re-run /voice:configure with a fresh pairing string.',
      })
    }
  } catch (err) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: `dispatcher_cert_pem is not a valid certificate PEM: ${err}`,
    })
  }
})
type Config = z.infer<typeof ConfigSchema>

function loadConfig(): Config {
  try {
    return ConfigSchema.parse(JSON.parse(readFileSync(CONFIG_FILE, 'utf8')))
  } catch (err) {
    const missing = err instanceof Error && (err as NodeJS.ErrnoException).code === 'ENOENT'
    const msg = missing
      ? `voice: no config at ${CONFIG_FILE}\n  run /voice:configure to set dispatcher URL and pairing string`
      : `voice: invalid config at ${CONFIG_FILE}: ${err}`
    process.stderr.write(`${msg}\n`)
    writeStatus({ state: 'error', last_error: msg })
    process.exit(1)
  }
}

const cfg = loadConfig()

// Load token: .env file first, then VOICE_DISPATCHER_TOKEN env var, then exit.
const token: string = (() => {
  const fromEnv = loadEnvToken() ?? process.env.VOICE_DISPATCHER_TOKEN
  if (!fromEnv) {
    process.stderr.write(
      `voice: no bearer token found.\n` +
      `  Expected VOICE_DISPATCHER_TOKEN in ${ENV_FILE}\n` +
      `  Run /voice:configure with the pairing string from 'voice-dispatcher config add-agent'\n`,
    )
    process.exit(1)
  }
  return fromEnv
})()

// ── MCP server ────────────────────────────────────────────────────────────────

const experimental: Record<string, object> = { 'claude/channel': {} }
if (cfg.enable_permission_relay) experimental['claude/channel/permission'] = {}

const mcp = new Server(
  { name: 'voice', version: '0.0.3' },
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
  const ReplyArgs = z.object({ utterance_id: z.string().min(1), text: z.string().min(1) })
  const parsed = ReplyArgs.safeParse(req.params.arguments)
  if (!parsed.success) {
    return {
      content: [{ type: 'text', text: `voice: invalid reply args: ${parsed.error.message}` }],
      isError: true,
    }
  }
  const args = parsed.data
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
let lastError: string | null = null
let permanentConnectFailure = false

async function connect(): Promise<void> {
  if (shuttingDown || permanentConnectFailure) return

  let ws: WS
  let opened = false
  try {
    const wsOpts = cfg.dispatcher_url.startsWith('wss://')
      ? {
          tls: {
            ca: cfg.dispatcher_cert_pem!,
            rejectUnauthorized: true,
            // Cert pinning is the identity check; hostnames vary across localhost, LAN, and Docker.
            checkServerIdentity: () => undefined,
          },
        }
      : {}
    ws = new WS(cfg.dispatcher_url, wsOpts as WS.ClientOptions)
  } catch (err) {
    lastError = String(err)
    process.stderr.write(`voice: failed to create WebSocket: ${lastError}\n`)
    writeStatus({ state: 'error', dispatcher_url: cfg.dispatcher_url, last_error: lastError })
    reconnectAttempt++
    const delay = Math.min(1_000 * reconnectAttempt, 30_000)
    setTimeout(connect, delay)
    return
  }
  socket = ws

  ws.on('open', () => {
    opened = true
    reconnectAttempt = 0
    lastError = null
    writeStatus({ state: 'connected', dispatcher_url: cfg.dispatcher_url })
    ws.send(JSON.stringify({ v: 1, type: 'hello', agent_id: cfg.agent_id, token }))

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

      case 'ping':
        if (ws.readyState === WS.OPEN) ws.send(JSON.stringify({ type: 'pong' }))
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
    if (permanentConnectFailure) return
    const statusFields: Record<string, unknown> = {
      state: 'disconnected',
      dispatcher_url: cfg.dispatcher_url,
      last_close_code: code,
      last_close_reason: reason.toString(),
    }
    if (lastError !== null) statusFields.last_error = lastError

    if (code === 4001) {
      permanentConnectFailure = true
      const msg = `authentication failed for ${cfg.dispatcher_url} — token was rejected. Re-run /voice:configure with a fresh pairing string from the dispatcher.`
      process.stderr.write(`voice: ${msg}\n`)
      writeStatus({ ...statusFields, state: 'error', last_error: msg })
      return
    }

    writeStatus(statusFields)
    if (!shuttingDown) {
      reconnectAttempt++
      const delay = Math.min(1_000 * reconnectAttempt, 30_000)
      process.stderr.write(`voice: disconnected — reconnecting in ${delay / 1_000}s\n`)
      setTimeout(connect, delay)
    }
  })

  ws.on('error', err => {
    lastError = err.message
    if (cfg.dispatcher_url.startsWith('wss://') && !opened && err.message.includes('TLS handshake failed')) {
      permanentConnectFailure = true
      const msg = `cert pin mismatch for ${cfg.dispatcher_url} — token was NOT sent. Re-run /voice:configure with the correct pairing string.`
      process.stderr.write(`voice: ${msg}\n`)
      writeStatus({ state: 'error', dispatcher_url: cfg.dispatcher_url, last_error: msg })
      return
    }
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
