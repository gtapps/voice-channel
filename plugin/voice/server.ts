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
import tls from 'node:tls'
import { createHash } from 'node:crypto'
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

function normalizePin(s: string): string {
  // Strip optional 'sha256:' prefix, colons, spaces, and lowercase → 64 hex chars
  return s.replace(/^sha256:/i, '').replace(/[:\s]/g, '').toLowerCase()
}

function verifyPin(url: string, pin: string): Promise<boolean> {
  // Parse host/port from the URL. Default port is 7355 (voice's dispatcher port),
  // not TLS's conventional 443.
  const parsed = new URL(url)
  const host = parsed.hostname
  const port = parsed.port ? parseInt(parsed.port, 10) : 7355

  // Do NOT set servername — it is SNI metadata, illegal as an IP literal, and
  // useless here (the dispatcher serves one cert and we validate by pinning, not
  // hostname). host already targets the connection; SNI plays no role.
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      sock.destroy()
      // Timeout = host unreachable/slow → reject (transient) — NOT a pin failure
      reject(new Error(`TLS preflight timed out connecting to ${host}:${port}`))
    }, 5_000)

    const sock = tls.connect({ host, port, rejectUnauthorized: false }, () => {
      clearTimeout(timer)
      const peerCert = sock.getPeerCertificate(true)
      const der = peerCert.raw
      const actual = createHash('sha256').update(der).digest('hex')
      sock.destroy()
      // resolve(false) = connected, read cert, hash ≠ pin → permanent pin mismatch
      // resolve(true)  = connected, read cert, hash = pin → open the real WS
      resolve(actual === normalizePin(pin))
    })

    sock.on('error', (err) => {
      clearTimeout(timer)
      // Socket error = host down/refused → reject (transient backoff)
      reject(err)
    })
  })
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
  enable_permission_relay: z.boolean().default(false),
}).superRefine((val, ctx) => {
  if (val.dispatcher_url.startsWith('wss://')) {
    if (!val.dispatcher_cert_sha256) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'dispatcher_cert_sha256 is required when dispatcher_url uses wss://',
      })
      return
    }
    const normalized = normalizePin(val.dispatcher_cert_sha256)
    if (!/^[0-9a-f]{64}$/.test(normalized)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'dispatcher_cert_sha256 must be a 64-char hex SHA-256 fingerprint (colons and sha256: prefix are stripped automatically)',
      })
    }
  }
})
type Config = z.infer<typeof ConfigSchema>

function loadConfig(): Config {
  try {
    return ConfigSchema.parse(JSON.parse(readFileSync(CONFIG_FILE, 'utf8')))
  } catch (err) {
    const missing = err instanceof Error && (err as NodeJS.ErrnoException).code === 'ENOENT'
    process.stderr.write(
      missing
        ? `voice: no config at ${CONFIG_FILE}\n  run /voice:configure to set dispatcher URL and pairing string\n`
        : `voice: invalid config at ${CONFIG_FILE}: ${err}\n`,
    )
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

// ── Status ────────────────────────────────────────────────────────────────────

function writeStatus(fields: Record<string, unknown>) {
  try {
    mkdirSync(STATE_DIR, { recursive: true })
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
  { name: 'voice', version: '0.0.1' },
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

async function connect(): Promise<void> {
  if (shuttingDown) return

  // WSS path: run TLS preflight to verify the dispatcher cert before sending the token.
  if (cfg.dispatcher_url.startsWith('wss://')) {
    const pin = cfg.dispatcher_cert_sha256! // guaranteed present by superRefine

    let matched: boolean
    try {
      matched = await verifyPin(cfg.dispatcher_url, pin)
    } catch (err) {
      // Preflight connection error (host down/refused/timeout) — transient, use backoff.
      lastError = String(err)
      process.stderr.write(`voice: TLS preflight failed (will retry): ${lastError}\n`)
      writeStatus({ state: 'disconnected', dispatcher_url: cfg.dispatcher_url, last_error: lastError })
      reconnectAttempt++
      const delay = Math.min(1_000 * reconnectAttempt, 30_000)
      setTimeout(connect, delay)
      return
    }

    if (!matched) {
      // Pin mismatch — permanent misconfiguration. Token was NOT sent. Do not reconnect.
      const msg = `cert pin mismatch for ${cfg.dispatcher_url} — token was NOT sent. Re-run /voice:configure with the correct pairing string.`
      process.stderr.write(`voice: ${msg}\n`)
      writeStatus({ state: 'error', dispatcher_url: cfg.dispatcher_url, last_error: msg })
      return // no reconnect
    }
  }

  let ws: WS
  try {
    // For wss://, rejectUnauthorized must be false — we pinned manually above.
    const wsOpts = cfg.dispatcher_url.startsWith('wss://')
      ? { tls: { rejectUnauthorized: false } }
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
    const statusFields: Record<string, unknown> = {
      state: 'disconnected',
      dispatcher_url: cfg.dispatcher_url,
      last_close_code: code,
      last_close_reason: reason.toString(),
    }
    if (lastError !== null) statusFields.last_error = lastError
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
