/**
 * Milestone 1 protocol spike — all six behaviors verified without real audio.
 *
 * Each test spawns server.ts as a subprocess and communicates via MCP's
 * stdio JSON-RPC framing (Content-Length headers). A minimal in-process
 * WebSocketServer mocks the dispatcher.
 */

import { describe, it, expect, beforeEach, afterEach, beforeAll, afterAll, vi } from 'vitest'
import { spawn, spawnSync, type ChildProcess } from 'child_process'
import { WebSocketServer, WebSocket } from 'ws'
import { readFileSync, mkdirSync, writeFileSync, mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { createServer } from 'net'
import { createHash, X509Certificate } from 'crypto'
import { createServer as createHttpsServer } from 'https'

const __dirname = dirname(fileURLToPath(import.meta.url))
const PLUGIN_ROOT = join(__dirname, '..')
const BUN = 'bun'

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

  // Write config — token goes in .env (the token is a credential).
  writeFileSync(
    join(dataDir, 'config.json'),
    JSON.stringify({
      dispatcher_url: `ws://127.0.0.1:${port}`,
      agent_id: 'test-agent',
      enable_permission_relay: opts.enablePermissionRelay ?? false,
    }),
  )
  writeFileSync(join(dataDir, '.env'), 'VOICE_DISPATCHER_TOKEN=test-token\n', { mode: 0o600 })

  // Start mock dispatcher
  const clients: WebSocket[] = []
  const wss = new WebSocketServer({ host: '127.0.0.1', port })
  await new Promise<void>(res => wss.once('listening', res))

  wss.on('connection', ws => clients.push(ws))

  // Spawn MCP server subprocess via bun (transpiles TS natively, resolves deps
  // from PLUGIN_ROOT/node_modules) — the same runtime the packaged plugin uses.
  const proc = spawn(BUN, [join(PLUGIN_ROOT, 'server.ts')], {
    cwd: PLUGIN_ROOT,
    env: {
      ...process.env,
      VOICE_STATE_DIR: dataDir,
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

describe('dispatcher ping → plugin pong (#9)', () => {
  let ctx: SetupResult

  beforeEach(async () => { ctx = await setup() }, 20_000)
  afterEach(() => ctx.cleanup())

  it('plugin responds pong to dispatcher ping on the same socket', async () => {
    const [wsClient] = ctx.wsClients()

    const pongPromise = new Promise<Record<string, unknown>>(res => {
      wsClient.on('message', raw => {
        const msg = JSON.parse(String(raw)) as Record<string, unknown>
        if (msg.type === 'pong') res(msg)
      })
    })

    wsClient.send(JSON.stringify({ type: 'ping' }))
    const pong = await pongPromise
    expect(pong.type).toBe('pong')
  })
})


describe('auth failure close 4001', () => {
  it('writes permanent auth error and does not reconnect', async () => {
    const port = await freePort()
    const dataDir = mkdtempSync(join(tmpdir(), 'voice-auth-fail-'))

    writeFileSync(
      join(dataDir, 'config.json'),
      JSON.stringify({
        dispatcher_url: `ws://127.0.0.1:${port}`,
        agent_id: 'test-agent',
        enable_permission_relay: false,
      }),
    )
    writeFileSync(join(dataDir, '.env'), 'VOICE_DISPATCHER_TOKEN=stale-token\n', { mode: 0o600 })

    let connectionCount = 0
    const hellos: unknown[] = []
    const wss = new WebSocketServer({ host: '127.0.0.1', port })
    await new Promise<void>(res => wss.once('listening', res))
    wss.on('connection', ws => {
      connectionCount++
      ws.on('message', raw => {
        const msg = JSON.parse(String(raw)) as Record<string, unknown>
        if (msg.type === 'hello') {
          hellos.push(msg)
          ws.close(4001, 'authentication failed')
        }
      })
    })

    const proc = spawn(BUN, [join(PLUGIN_ROOT, 'server.ts')], {
      cwd: PLUGIN_ROOT,
      env: { ...process.env, VOICE_STATE_DIR: dataDir, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT },
      stdio: ['pipe', 'pipe', 'pipe'],
    })

    proc.stdin!.write(frame({ jsonrpc: '2.0', id: 1, method: 'initialize',
      params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'test', version: '0' } } }))

    const status = await new Promise<Record<string, unknown>>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('auth status never written')), 3_000)
      const interval = setInterval(() => {
        try {
          const st = JSON.parse(readFileSync(join(dataDir, 'status.json'), 'utf8')) as Record<string, unknown>
          if (st.last_close_code === 4001) {
            clearTimeout(timeout)
            clearInterval(interval)
            resolve(st)
          }
        } catch { /* not written yet */ }
      }, 50)
    })

    await new Promise(r => setTimeout(r, 1_200))

    expect(hellos).toHaveLength(1)
    expect(connectionCount).toBe(1)
    expect(status.state).toBe('error')
    expect(status.last_close_code).toBe(4001)
    expect(String(status.last_error ?? '')).toMatch(/token was rejected/i)
    expect(String(status.last_error ?? '')).toMatch(/voice:configure/i)

    proc.kill(); wss.close()
    try { rmSync(dataDir, { recursive: true }) } catch { /* ignore */ }
  }, 20_000)
})

describe('reply tool: invalid args rejected (#10)', () => {
  let ctx: SetupResult

  beforeEach(async () => { ctx = await setup() }, 20_000)
  afterEach(() => ctx.cleanup())

  it('missing utterance_id returns isError and no speak frame', async () => {
    const [wsClient] = ctx.wsClients()

    const speakFrames: unknown[] = []
    wsClient.on('message', raw => {
      const msg = JSON.parse(String(raw)) as Record<string, unknown>
      if (msg.type === 'speak') speakFrames.push(msg)
    })

    const resp = await ctx.send('tools/call', {
      name: 'reply',
      arguments: { text: 'hello' }, // utterance_id missing
    })
    expect((resp as { result?: { isError?: boolean } }).result?.isError).toBe(true)
    // Give a brief window for any spurious speak frame to arrive
    await new Promise(r => setTimeout(r, 100))
    expect(speakFrames).toHaveLength(0)
  })

  it('empty text returns isError and no speak frame', async () => {
    const [wsClient] = ctx.wsClients()

    const speakFrames: unknown[] = []
    wsClient.on('message', raw => {
      const msg = JSON.parse(String(raw)) as Record<string, unknown>
      if (msg.type === 'speak') speakFrames.push(msg)
    })

    const resp = await ctx.send('tools/call', {
      name: 'reply',
      arguments: { utterance_id: 'u-1', text: '' },
    })
    expect((resp as { result?: { isError?: boolean } }).result?.isError).toBe(true)
    await new Promise(r => setTimeout(r, 100))
    expect(speakFrames).toHaveLength(0)
  })
})

describe('bad dispatcher URL rejected at startup (R6)', () => {
  it('exits non-zero when dispatcher_url does not start with ws://', () => {
    // Write a config with an http:// URL — the zod schema rejects it at load time
    const port = 19999 // unused; process should exit before connecting
    const dataDir = mkdtempSync(join(tmpdir(), 'voice-badurl-'))
    writeFileSync(
      join(dataDir, 'config.json'),
      JSON.stringify({
        dispatcher_url: `http://127.0.0.1:${port}`,
        agent_id: 'test',
        enable_permission_relay: false,
      }),
    )
    writeFileSync(join(dataDir, '.env'), 'VOICE_DISPATCHER_TOKEN=tok\n', { mode: 0o600 })
    const result = spawnSync(BUN, [join(PLUGIN_ROOT, 'server.ts')], {
      cwd: PLUGIN_ROOT,
      env: { ...process.env, VOICE_STATE_DIR: dataDir, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT },
      timeout: 5_000,
      encoding: 'utf8',
    })
    try { rmSync(dataDir, { recursive: true }) } catch { /* ignore */ }
    expect(result.status, result.stderr).not.toBe(0)
    expect(result.stderr).toMatch(/dispatcher_url must start with ws/)
  })
})

// ── WSS pinning tests ─────────────────────────────────────────────────────────

describe('WSS certificate pinning', () => {
  let certPem: string
  let keyPem: string
  let wrongCertPem: string
  let realPin: string   // correct SHA-256 fingerprint (64 hex chars)
  let wrongPin: string  // fingerprint for wrongCertPem
  let bogusPin: string  // wrong fingerprint

  let _tlsDir: string

  beforeAll(() => {
    // Generate a self-signed cert with openssl into a temp directory.
    _tlsDir = mkdtempSync(join(tmpdir(), 'voice-wss-certs-'))
    const certPath = join(_tlsDir, 'cert.pem')
    const keyPath  = join(_tlsDir, 'key.pem')
    const wrongCertPath = join(_tlsDir, 'wrong-cert.pem')
    const wrongKeyPath  = join(_tlsDir, 'wrong-key.pem')

    for (const [outCert, outKey, cn] of [
      [certPath, keyPath, 'voice-test'],
      [wrongCertPath, wrongKeyPath, 'wrong-voice-test'],
    ]) {
      const r = spawnSync('openssl', [
        'req', '-x509', '-newkey', 'rsa:2048',
        '-keyout', outKey,
        '-out', outCert,
        '-days', '1', '-nodes',
        '-subj', `/CN=${cn}`,
        '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
      ], { encoding: 'utf8', timeout: 15_000 })
      if (r.status !== 0) throw new Error(`openssl failed: ${r.stderr}`)
    }

    certPem = readFileSync(certPath, 'utf8')
    keyPem  = readFileSync(keyPath, 'utf8')
    wrongCertPem = readFileSync(wrongCertPath, 'utf8')

    // Compute expected pin via X509Certificate.raw (same fingerprint path as server.ts)
    const x509 = new X509Certificate(certPem)
    const wrongX509 = new X509Certificate(wrongCertPem)
    realPin = createHash('sha256').update(x509.raw).digest('hex')
    wrongPin = createHash('sha256').update(wrongX509.raw).digest('hex')
    bogusPin = 'a'.repeat(64)
  })

  afterAll(() => {
    try { rmSync(_tlsDir, { recursive: true }) } catch { /* ignore */ }
  })

  it('connects and sends hello when pin matches', async () => {
    const port = await freePort()
    const dataDir = mkdtempSync(join(tmpdir(), 'voice-wss-match-'))

    // Start WSS server with the cert that the plugin pins in config.
    const httpsServer = createHttpsServer({ cert: certPem, key: keyPem })
    const wss = new WebSocketServer({ server: httpsServer })
    await new Promise<void>(res => httpsServer.listen(port, '127.0.0.1', res))

    const hellos: unknown[] = []
    wss.on('connection', ws => {
      ws.on('message', raw => {
        try {
          const msg = JSON.parse(String(raw))
          if (msg.type === 'hello') hellos.push(msg)
        } catch { /* ignore */ }
      })
    })

    writeFileSync(
      join(dataDir, 'config.json'),
      JSON.stringify({
        dispatcher_url: `wss://127.0.0.1:${port}`,
        agent_id: 'test-agent',
        dispatcher_cert_sha256: realPin,
        dispatcher_cert_pem: certPem,
        enable_permission_relay: false,
      }),
    )
    writeFileSync(join(dataDir, '.env'), 'VOICE_DISPATCHER_TOKEN=test-token\n', { mode: 0o600 })

    const proc = spawn(BUN, [join(PLUGIN_ROOT, 'server.ts')], {
      cwd: PLUGIN_ROOT,
      env: { ...process.env, VOICE_STATE_DIR: dataDir, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT },
      stdio: ['pipe', 'pipe', 'pipe'],
    })

    // MCP initialize so server.ts starts up fully
    proc.stdin!.write(frame({ jsonrpc: '2.0', id: 1, method: 'initialize',
      params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'test', version: '0' } } }))

    // Wait up to 5s for a hello
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('hello never received')), 5_000)
      const check = setInterval(() => {
        if (hellos.length > 0) { clearTimeout(t); clearInterval(check); resolve() }
      }, 50)
    })

    expect(hellos).toHaveLength(1)
    expect((hellos[0] as Record<string, unknown>).type).toBe('hello')

    proc.kill(); wss.close(); httpsServer.close()
    try { rmSync(dataDir, { recursive: true }) } catch { /* ignore */ }
  }, 20_000)

  it('does NOT send hello and writes pin error to status.json when pinned PEM mismatches', async () => {
    const port = await freePort()
    const dataDir = mkdtempSync(join(tmpdir(), 'voice-wss-mismatch-'))

    const httpsServer = createHttpsServer({ cert: certPem, key: keyPem })
    const wss = new WebSocketServer({ server: httpsServer })
    await new Promise<void>(res => httpsServer.listen(port, '127.0.0.1', res))

    const hellos: unknown[] = []
    wss.on('connection', ws => {
      ws.on('message', raw => {
        try {
          const msg = JSON.parse(String(raw))
          if (msg.type === 'hello') hellos.push(msg)
        } catch { /* ignore */ }
      })
    })

    writeFileSync(
      join(dataDir, 'config.json'),
      JSON.stringify({
        dispatcher_url: `wss://127.0.0.1:${port}`,
        agent_id: 'test-agent',
        dispatcher_cert_sha256: wrongPin,
        dispatcher_cert_pem: wrongCertPem,
        enable_permission_relay: false,
      }),
    )
    writeFileSync(join(dataDir, '.env'), 'VOICE_DISPATCHER_TOKEN=test-token\n', { mode: 0o600 })

    const proc = spawn(BUN, [join(PLUGIN_ROOT, 'server.ts')], {
      cwd: PLUGIN_ROOT,
      env: { ...process.env, VOICE_STATE_DIR: dataDir, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT },
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    proc.stdin!.write(frame({ jsonrpc: '2.0', id: 1, method: 'initialize',
      params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'test', version: '0' } } }))

    // Wait 2s — TLS should fail before open, so no hello is sent.
    await new Promise(r => setTimeout(r, 2_000))

    expect(hellos).toHaveLength(0)

    // status.json should reflect the pin error
    const statusPath = join(dataDir, 'status.json')
    let status: Record<string, unknown> = {}
    try { status = JSON.parse(readFileSync(statusPath, 'utf8')) } catch { /* ok if missing */ }
    expect(status.state).toBe('error')
    expect(String(status.last_error ?? '')).toMatch(/pin mismatch/i)

    proc.kill(); wss.close(); httpsServer.close()
    try { rmSync(dataDir, { recursive: true }) } catch { /* ignore */ }
  }, 20_000)

  it('keeps connection failures retryable instead of treating them as pin failures', async () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'voice-wss-refused-'))
    writeFileSync(
      join(dataDir, 'config.json'),
      JSON.stringify({
        dispatcher_url: 'wss://127.0.0.1:9',
        agent_id: 'test-agent',
        dispatcher_cert_sha256: realPin,
        dispatcher_cert_pem: certPem,
        enable_permission_relay: false,
      }),
    )
    writeFileSync(join(dataDir, '.env'), 'VOICE_DISPATCHER_TOKEN=test-token\n', { mode: 0o600 })

    const proc = spawn(BUN, [join(PLUGIN_ROOT, 'server.ts')], {
      cwd: PLUGIN_ROOT,
      env: { ...process.env, VOICE_STATE_DIR: dataDir, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT },
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    proc.stdin!.write(frame({ jsonrpc: '2.0', id: 1, method: 'initialize',
      params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'test', version: '0' } } }))

    await new Promise(r => setTimeout(r, 1_000))

    const status = JSON.parse(readFileSync(join(dataDir, 'status.json'), 'utf8')) as Record<string, unknown>
    expect(status.state).toBe('disconnected')
    expect(String(status.last_error ?? '')).toMatch(/Failed to connect/i)
    expect(String(status.last_error ?? '')).not.toMatch(/pin mismatch/i)

    proc.kill()
    try { rmSync(dataDir, { recursive: true }) } catch { /* ignore */ }
  }, 20_000)

  it('exits non-zero at load when wss:// config is missing dispatcher_cert_pem', () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'voice-wss-nopin-'))
    writeFileSync(
      join(dataDir, 'config.json'),
      JSON.stringify({
        dispatcher_url: 'wss://127.0.0.1:7355',
        agent_id: 'test',
        dispatcher_cert_sha256: bogusPin,
        // no dispatcher_cert_pem — superRefine must reject legacy v1 config
        enable_permission_relay: false,
      }),
    )
    writeFileSync(join(dataDir, '.env'), 'VOICE_DISPATCHER_TOKEN=tok\n', { mode: 0o600 })

    const result = spawnSync(BUN, [join(PLUGIN_ROOT, 'server.ts')], {
      cwd: PLUGIN_ROOT,
      env: { ...process.env, VOICE_STATE_DIR: dataDir, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT },
      timeout: 5_000,
      encoding: 'utf8',
    })
    try { rmSync(dataDir, { recursive: true }) } catch { /* ignore */ }
    expect(result.status, result.stderr).not.toBe(0)
    expect(result.stderr).toMatch(/dispatcher_cert_pem/i)
    expect(result.stderr).toMatch(/v2 pairing/i)
  })

  it('exits non-zero at load when dispatcher_cert_pem and dispatcher_cert_sha256 disagree', () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'voice-wss-pin-mismatch-'))
    writeFileSync(
      join(dataDir, 'config.json'),
      JSON.stringify({
        dispatcher_url: 'wss://127.0.0.1:7355',
        agent_id: 'test',
        dispatcher_cert_sha256: bogusPin,
        dispatcher_cert_pem: certPem,
        enable_permission_relay: false,
      }),
    )
    writeFileSync(join(dataDir, '.env'), 'VOICE_DISPATCHER_TOKEN=tok\n', { mode: 0o600 })

    const result = spawnSync(BUN, [join(PLUGIN_ROOT, 'server.ts')], {
      cwd: PLUGIN_ROOT,
      env: { ...process.env, VOICE_STATE_DIR: dataDir, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT },
      timeout: 5_000,
      encoding: 'utf8',
    })
    const status = JSON.parse(readFileSync(join(dataDir, 'status.json'), 'utf8')) as Record<string, unknown>
    try { rmSync(dataDir, { recursive: true }) } catch { /* ignore */ }
    expect(result.status, result.stderr).not.toBe(0)
    expect(result.stderr).toMatch(/does not match dispatcher_cert_sha256/i)
    expect(status.state).toBe('error')
    expect(String(status.last_error ?? '')).toMatch(/does not match dispatcher_cert_sha256/i)
  })
})

describe('runtime: bun resolves channel deps', () => {
  it('bun resolves the SDK, ws, and zod from node_modules', () => {
    // The packaged plugin runs `bun server.ts` (via the start script, which
    // `bun install`s first). This verifies bun resolves the three runtime deps
    // from PLUGIN_ROOT/node_modules — no tsx/esbuild, no bootstrap step.
    const result = spawnSync(
      BUN,
      [
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
        cwd: PLUGIN_ROOT,
        timeout: 10_000,
        encoding: 'utf8',
      },
    )
    expect(result.status, result.stderr).toBe(0)
  })
})
