/**
 * Milestone 1 protocol spike — all six behaviors verified without real audio.
 *
 * Each test spawns server.ts as a subprocess and communicates via MCP's
 * stdio JSON-RPC framing (Content-Length headers). A minimal in-process
 * WebSocketServer mocks the dispatcher.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { spawn, spawnSync, type ChildProcess } from 'child_process'
import { WebSocketServer, WebSocket } from 'ws'
import { mkdirSync, writeFileSync, mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { createServer } from 'net'

const __dirname = dirname(fileURLToPath(import.meta.url))
const PLUGIN_ROOT = join(__dirname, '..')
const NODE = process.execPath

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Find a free TCP port */
function freePort(): Promise<number> {
  return new Promise((res, rej) => {
    const srv = createServer()
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address() as { port: number }
      srv.close(() => res(port))
    })
    srv.on('error', rej)
  })
}

/** Encode a JSON-RPC message as newline-delimited JSON (MCP SDK stdio format) */
function frame(obj: Record<string, unknown>): Buffer {
  return Buffer.from(JSON.stringify(obj) + '\n')
}

/** Parse newline-delimited JSON messages from a buffer, return parsed messages + leftover */
function parseFrames(buf: string): { messages: Record<string, unknown>[]; rest: string } {
  const messages: Record<string, unknown>[] = []
  let s = buf
  for (;;) {
    const nl = s.indexOf('\n')
    if (nl === -1) break
    const line = s.slice(0, nl).replace(/\r$/, '')
    s = s.slice(nl + 1)
    if (line.trim()) messages.push(JSON.parse(line))
  }
  return { messages, rest: s }
}

type SetupResult = {
  proc: ChildProcess
  send: (method: string, params: Record<string, unknown>) => Promise<Record<string, unknown>>
  notify: (method: string, params: Record<string, unknown>) => void
  waitForNotification: (method: string, timeoutMs?: number) => Promise<Record<string, unknown>>
  wss: WebSocketServer
  wsClients: () => WebSocket[]
  dataDir: string
  cleanup: () => void
}

/**
 * Start a mock dispatcher WS server + MCP server subprocess.
 * Returns helpers to drive the test.
 */
async function setup(opts: { enablePermissionRelay?: boolean } = {}): Promise<SetupResult> {
  const port = await freePort()
  const dataDir = mkdtempSync(join(tmpdir(), 'voice-test-'))

  // Write config
  writeFileSync(
    join(dataDir, 'config.json'),
    JSON.stringify({
      dispatcher_url: `ws://127.0.0.1:${port}`,
      token: 'test-token',
      hermit_id: 'test-hermit',
      enable_permission_relay: opts.enablePermissionRelay ?? false,
    }),
  )

  // Start mock dispatcher
  const clients: WebSocket[] = []
  const wss = new WebSocketServer({ host: '127.0.0.1', port })
  await new Promise<void>(res => wss.once('listening', res))

  wss.on('connection', ws => clients.push(ws))

  // Spawn MCP server subprocess.
  // CWD must be PLUGIN_ROOT (has node_modules/tsx) so that `--import tsx` resolves.
  // In production this is CLAUDE_PLUGIN_DATA after bootstrap.sh's npm install.
  const proc = spawn(NODE, ['--import', 'tsx', join(PLUGIN_ROOT, 'server.ts')], {
    cwd: PLUGIN_ROOT,
    env: {
      ...process.env,
      CLAUDE_PLUGIN_DATA: dataDir,
      CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT,
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  })

  // Capture stderr for diagnostics (timeouts are silent otherwise)
  proc.stderr!.on('data', (chunk: Buffer) => {
    process.stderr.write(`[server.ts] ${chunk.toString()}`)
  })

  // Buffer stdout for JSON-RPC framing
  let buf = ''
  const pending = new Map<number, (r: Record<string, unknown>) => void>()
  const notifQueue: Record<string, unknown>[] = []
  const notifWaiters: Array<{ method: string; resolve: (n: Record<string, unknown>) => void }> = []
  let reqId = 0

  proc.stdout!.on('data', (chunk: Buffer) => {
    buf += chunk.toString()
    const { messages, rest } = parseFrames(buf)
    buf = rest
    for (const msg of messages) {
      if ('id' in msg && pending.has(msg.id as number)) {
        pending.get(msg.id as number)!(msg)
        pending.delete(msg.id as number)
      } else if ('method' in msg && !('id' in msg)) {
        // Notification
        const waiting = notifWaiters.findIndex(w => w.method === msg.method)
        if (waiting !== -1) {
          notifWaiters.splice(waiting, 1)[0].resolve(msg)
        } else {
          notifQueue.push(msg)
        }
      }
    }
  })

  function send(method: string, params: Record<string, unknown>): Promise<Record<string, unknown>> {
    const id = ++reqId
    return new Promise(resolve => {
      pending.set(id, resolve)
      proc.stdin!.write(frame({ jsonrpc: '2.0', id, method, params }))
    })
  }

  function notify(method: string, params: Record<string, unknown>) {
    proc.stdin!.write(frame({ jsonrpc: '2.0', method, params }))
  }

  function waitForNotification(method: string, timeoutMs = 3000): Promise<Record<string, unknown>> {
    // Check if already queued
    const idx = notifQueue.findIndex(n => n.method === method)
    if (idx !== -1) return Promise.resolve(notifQueue.splice(idx, 1)[0])
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`timed out waiting for ${method}`)), timeoutMs)
      notifWaiters.push({
        method,
        resolve: n => { clearTimeout(t); resolve(n) },
      })
    })
  }

  // Perform MCP initialize handshake
  const initResp = await send('initialize', {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'test', version: '0' },
  })
  expect((initResp as { result?: { serverInfo?: { name?: string } } }).result?.serverInfo?.name).toBe('voice')
  notify('notifications/initialized', {})

  // Wait for server.ts to send the hello message (confirms ws.on('open') ran and socket is OPEN)
  await new Promise<void>((res, rej) => {
    const t = setTimeout(() => rej(new Error('WS hello never received')), 3000)
    const onHello = () => { clearTimeout(t); res() }
    if (clients.length > 0) {
      clients[clients.length - 1].once('message', onHello)
    } else {
      wss.once('connection', ws => ws.once('message', onHello))
    }
  })

  function cleanup() {
    proc.stdin!.end()
    proc.kill()
    wss.close()
    try { rmSync(dataDir, { recursive: true }) } catch { /* ignore */ }
  }

  return {
    proc,
    send,
    notify,
    waitForNotification,
    wss,
    wsClients: () => clients,
    dataDir,
    cleanup,
  }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('server name and capabilities', () => {
  let ctx: SetupResult

  beforeEach(async () => { ctx = await setup() }, 20_000)
  afterEach(() => ctx.cleanup())

  it('server name is "voice"', async () => {
    const resp = await ctx.send('initialize', {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'test2', version: '0' },
    })
    const name = (resp as { result?: { serverInfo?: { name?: string } } }).result?.serverInfo?.name
    expect(name).toBe('voice')
  })

  it('experimental.claude/channel capability is declared', async () => {
    const resp = await ctx.send('initialize', {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'test3', version: '0' },
    })
    const exp = (resp as { result?: { capabilities?: { experimental?: Record<string, unknown> } } })
      .result?.capabilities?.experimental
    expect(exp).toHaveProperty('claude/channel')
  })

  it('claude/channel/permission NOT declared when enable_permission_relay=false', async () => {
    const resp = await ctx.send('initialize', {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'test4', version: '0' },
    })
    const exp = (resp as { result?: { capabilities?: { experimental?: Record<string, unknown> } } })
      .result?.capabilities?.experimental
    expect(exp).not.toHaveProperty('claude/channel/permission')
  })
})

describe('reply tool schema', () => {
  let ctx: SetupResult

  beforeEach(async () => { ctx = await setup() }, 20_000)
  afterEach(() => ctx.cleanup())

  it('tools/list returns reply with utterance_id + text', async () => {
    const resp = await ctx.send('tools/list', {})
    const tools = (resp as { result?: { tools?: { name: string; inputSchema: Record<string, unknown> }[] } })
      .result?.tools
    expect(tools).toBeDefined()
    const reply = tools!.find(t => t.name === 'reply')
    expect(reply).toBeDefined()
    const props = (reply!.inputSchema as { properties?: Record<string, unknown> }).properties ?? {}
    expect(props).toHaveProperty('utterance_id')
    expect(props).toHaveProperty('text')
    const required = (reply!.inputSchema as { required?: string[] }).required ?? []
    expect(required).toContain('utterance_id')
    expect(required).toContain('text')
  })
})

describe('outbound: transcript → notifications/claude/channel', () => {
  let ctx: SetupResult

  beforeEach(async () => { ctx = await setup() }, 20_000)
  afterEach(() => ctx.cleanup())

  it('sends notifications/claude/channel with utterance_id in meta', async () => {
    const notifPromise = ctx.waitForNotification('notifications/claude/channel')

    // Mock dispatcher sends a transcript
    const [wsClient] = ctx.wsClients()
    wsClient.send(JSON.stringify({
      type: 'transcript',
      utterance_id: 'u-test-001',
      text: 'turn on the lights',
      lang: 'en',
      trigger: 'hey jarvis',
      ts: new Date().toISOString(),
    }))

    const notif = await notifPromise
    const params = (notif as { params?: { content?: string; meta?: Record<string, unknown> } }).params
    expect(params?.content).toBe('turn on the lights')
    expect(params?.meta?.utterance_id).toBe('u-test-001')
    expect(params?.meta?.trigger).toBe('hey jarvis')
  })
})

describe('inbound: tools/call reply → ws speak', () => {
  let ctx: SetupResult

  beforeEach(async () => { ctx = await setup() }, 20_000)
  afterEach(() => ctx.cleanup())

  it('reply tool call sends speak frame to dispatcher', async () => {
    const [wsClient] = ctx.wsClients()

    const speakPromise = new Promise<Record<string, unknown>>(res => {
      wsClient.on('message', raw => {
        const msg = JSON.parse(String(raw)) as Record<string, unknown>
        if (msg.type === 'speak') res(msg)
      })
    })

    const toolResp = await ctx.send('tools/call', {
      name: 'reply',
      arguments: { utterance_id: 'u-test-002', text: 'Lights are now on.' },
    })
    expect((toolResp as { result?: { isError?: boolean } }).result?.isError).toBeFalsy()

    const speak = await speakPromise
    expect(speak.type).toBe('speak')
    expect(speak.utterance_id).toBe('u-test-002')
    expect(speak.text).toBe('Lights are now on.')
  })
})

describe('permission relay (opt-in)', () => {
  let ctx: SetupResult

  beforeEach(async () => { ctx = await setup({ enablePermissionRelay: true }) }, 20_000)
  afterEach(() => ctx.cleanup())

  it('claude/channel/permission IS declared when enable_permission_relay=true', async () => {
    const resp = await ctx.send('initialize', {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'test-perm', version: '0' },
    })
    const exp = (resp as { result?: { capabilities?: { experimental?: Record<string, unknown> } } })
      .result?.capabilities?.experimental
    expect(exp).toHaveProperty('claude/channel/permission')
  })

  it('inbound permission_request is forwarded to dispatcher as ws frame', async () => {
    const [wsClient] = ctx.wsClients()

    const permFramePromise = new Promise<Record<string, unknown>>(res => {
      wsClient.on('message', raw => {
        const msg = JSON.parse(String(raw)) as Record<string, unknown>
        if (msg.type === 'permission_request') res(msg)
      })
    })

    ctx.notify('notifications/claude/channel/permission_request', {
      request_id: 'abcde',
      tool_name: 'Bash',
      description: 'Run a shell command',
      input_preview: '{"command":"pwd"}',
    })

    const frame = await permFramePromise
    expect(frame.type).toBe('permission_request')
    expect(frame.tool_name).toBe('Bash')
    expect(frame.request_id).toBe('abcde')
  })
})

describe('bootstrap module resolution', () => {
  it('node --import tsx resolves from CWD node_modules', () => {
    // Verifies the bootstrap.sh invariant:
    //   cd "$PLUGIN_DATA"
    //   exec node --import tsx "$PLUGIN_ROOT/server.ts"
    //
    // Node's ESM loader resolves bare specifiers (including `tsx` in --import)
    // from CWD's node_modules. When CWD has node_modules with tsx and the
    // channel deps, everything resolves without any additional NODE_PATH tricks.
    //
    // In tests we use PLUGIN_ROOT as a stand-in for PLUGIN_DATA because PLUGIN_ROOT
    // already has node_modules after `npm install`. In production, PLUGIN_DATA gets
    // them from bootstrap.sh's `npm install --prefix "$PLUGIN_DATA" "$PLUGIN_ROOT"`.
    const result = spawnSync(
      NODE,
      [
        '--import', 'tsx',
        '--input-type=module',
        '--eval',
        [
          "import('@modelcontextprotocol/sdk/server/index.js')",
          ".then(() => import('ws'))",
          ".then(() => import('zod'))",
          ".then(() => process.exit(0))",
          ".catch(e => { process.stderr.write(e.message + '\\n'); process.exit(1) })",
        ].join(''),
      ],
      {
        cwd: PLUGIN_ROOT,   // has node_modules — simulates PLUGIN_DATA after npm install
        timeout: 10_000,
        encoding: 'utf8',
      },
    )
    expect(result.status, result.stderr).toBe(0)
  })
})
