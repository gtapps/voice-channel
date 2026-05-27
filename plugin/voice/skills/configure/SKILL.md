---
name: voice:configure
description: Configure the voice channel connection — dispatcher URL, pairing string (bundles agent ID, token, and dispatcher certificate), and optional permission-relay opt-in.
allowed-tools:
  - AskUserQuestion
  - Read
  - Write
  - Bash(echo *)
  - Bash(mkdir *)
  - Bash(bun -e *)
  - Bash(chmod *)
---

# /voice:configure

Configure the voice channel connection inside this agent container.

## Resolve the state dir

Run:

```bash
echo "${VOICE_STATE_DIR:-${CLAUDE_PROJECT_DIR:-$(pwd)}/.claude/channels/voice}"
```

Use the output as `<STATE_DIR>` for every file path below. When `VOICE_STATE_DIR` is already set
it wins; otherwise the state dir defaults into the project Claude Code was started in
(`<project>/.claude/channels/voice`), and the "Pin the state dir" step below makes that durable.

## Detect existing config

Check if `<STATE_DIR>/config.json` exists. If it does, read it and record the
current values for `dispatcher_url`, `agent_id`, `dispatcher_cert_sha256`, `dispatcher_cert_pem`, and
`enable_permission_relay`. Tell the user:
_"Found existing config — showing current values as defaults."_

## Collect settings

### Call 1 — Dispatcher URL + permission relay

Ask both in a single `AskUserQuestion` call:

```
questions: [
  {
    header: "Dispatcher URL",
    question: "WebSocket URL of the voice-dispatcher on your laptop?",
    options: [
      // Always show the secure default first.
      // If existing config differs from the default, replace option 2 with the current value.
      // If existing config matches or there is no config, use the LAN IP fallback.
      { label: "wss://127.0.0.1:7355", description: "localhost — default (dispatcher on the same machine)" },
      { label: "<current value OR 'wss://laptop.local:7355'>", description: "<'Current value' OR 'Remote / Docker — mDNS hostname'>" }
    ]
  },
  {
    header: "Permission relay",
    question: "Relay Claude's tool-permission prompts through the voice channel?",
    options: [
      { label: "No — keep off", description: "Terminal approval only. Safest — anyone the mic can hear could otherwise say 'yes <id>' and approve a tool call. (default)" },
      { label: "Yes — enable", description: "⚠ Voice approval is unauthenticated. Only enable if you accept that risk and understand terminal approval is always the fallback." }
    ]
  }
]
```

### Call 2 — Pairing string

Ask alone so the user can focus on pasting it:

```
questions: [
  {
    header: "Pairing string",
    question: "Paste the pairing string printed by the dispatcher:",
    options: [
      // Include ONLY if existing config has agent_id, dispatcher_cert_sha256, dispatcher_cert_pem
      // AND the .env file has a token:
      { label: "Keep existing pairing", description: "Leave the current agent ID, token, and dispatcher certificate unchanged" },
      { label: "I don't have the pairing string yet", description: "Run voice-dispatcher config add-agent <id> --triggers '...' --voice <voice.onnx> on the dispatcher first, then re-run /voice:configure." }
    ]
    // User pastes the actual voicepair_... string via Other
  }
]
```

Handle the result:

- **"Keep existing pairing"** → keep `agent_id`, `dispatcher_cert_sha256`, `dispatcher_cert_pem`, and the token in `.env` unchanged
- **"I don't have the pairing string yet"** → stop here; remind the user to run
  `voice-dispatcher config add-agent <agent_id> --triggers "..."` on their laptop, then re-run `/voice:configure`
- **Other (typed value starting with `voicepair_`)** → decode it (see below)

## Decode and verify the pairing string

Use Bun (the plugin's guaranteed runtime — not coreutils `base64`, which may be absent in minimal
containers and cannot decode url-safe base64). This single command decodes the pairing string,
verifies the embedded cert against its fingerprint (mirroring the plugin's own pin check —
`sha256` of the cert's DER bytes), and prints the fields you need as JSON:

```bash
bun -e '
const {createHash,X509Certificate}=require("node:crypto");
const raw=process.argv[1].replace(/^voicepair_/,"");
const p=JSON.parse(Buffer.from(raw,"base64url").toString());
if(p.pairing_v!==2){console.error("ERROR: pairing_v is not 2 — re-run voice-dispatcher config add-agent / rotate-token");process.exit(1);}
const want=(p.cert_sha256||"").replace(/^sha256:/i,"").replace(/[:\s]/g,"").toLowerCase();
const got=createHash("sha256").update(new X509Certificate(p.cert_pem).raw).digest("hex");
if(want!==got){console.error("ERROR: cert_pem does not match cert_sha256 — ask for a fresh pairing string");process.exit(1);}
console.log(JSON.stringify({agent_id:p.agent_id,token:p.token,cert_sha256:p.cert_sha256,cert_pem:p.cert_pem},null,2));
' "<pairing-string>"
```

**Do this verification only via this Bun command — never inspect or parse the PEM text yourself**
(hand-parsing the PEM is what produces spurious "the pem has some lines non-standard length"
errors). If the command exits non-zero, stop and relay its `ERROR:` message; do not write any files.

On success, take the printed JSON fields:

- `agent_id` — the agent's ID on the dispatcher
- `token` — the bearer token (a credential — write to `.env`, not `config.json`)
- `cert_sha256` — the dispatcher's TLS cert fingerprint (public — write to `config.json`)
- `cert_pem` — the dispatcher's public TLS certificate PEM (public — write to `config.json` as `dispatcher_cert_pem`)

## Write `.env` and `config.json`

Create `<STATE_DIR>` if it does not exist, then:

**`<STATE_DIR>/.env`** — the token is a credential:

```
VOICE_DISPATCHER_TOKEN=<token>
```

```bash
chmod 600 "<STATE_DIR>/.env"
```

**`<STATE_DIR>/config.json`**:

```json
{
  "dispatcher_url": "<dispatcher_url>",
  "agent_id": "<agent_id>",
  "dispatcher_cert_sha256": "<cert_sha256>",
  "dispatcher_cert_pem": "<cert_pem>",
  "enable_permission_relay": <enable_permission_relay>
}
```

## Pin the state dir

So the next session's MCP server resolves the same `<STATE_DIR>`, pin `VOICE_STATE_DIR` into the
project's local settings (Claude Code injects this `env` block into every MCP server it spawns).
Merge it in without clobbering existing keys — use Bun:

```bash
bun -e '
const fs=require("fs"), path=require("path");
const f=process.argv[1], dir=process.argv[2];
let s={}; try { s=JSON.parse(fs.readFileSync(f,"utf8")); } catch(e) { if(e.code!=="ENOENT"){console.error("ERROR: could not read/parse "+f+" — fix or remove it, then re-run: "+e.message);process.exit(1);} }
s.env={...(s.env||{}), VOICE_STATE_DIR:dir};
fs.mkdirSync(path.dirname(f),{recursive:true});
fs.writeFileSync(f, JSON.stringify(s,null,2)+"\n");
' "${CLAUDE_PROJECT_DIR:-$(pwd)}/.claude/settings.local.json" "<STATE_DIR>"
```

The state dir holds the bearer token in `.env`, so keep it out of git — write `<STATE_DIR>/.gitignore`
containing a single line `*` (use the Write tool). This is harmless if `<STATE_DIR>` lives outside
any repo.

## After writing

Tell the user:

- `VOICE_STATE_DIR` is now pinned in `.claude/settings.local.json`, so the MCP server and both
  skills resolve this config automatically from the next session start — no manual env setup needed.
- To activate the channel, close this session and start a new one with:
  ```
  claude --dangerously-load-development-channels plugin:voice@voice-channel
  ```
- This skill only configures the plugin inside this container — it does NOT modify
  the dispatcher's config on the laptop.

## Notes

- This skill writes the bearer token to `<STATE_DIR>/.env` (chmod 600) and the
  rest of the config to `<STATE_DIR>/config.json`. The token never appears in
  `config.json`.
- The v2 pairing string bundles `agent_id` + `token` + `cert_sha256` + `cert_pem` in one
  paste-safe string. It is printed by `voice-dispatcher config add-agent` on the
  laptop. The PEM is public certificate material; the token remains the only secret.
- To add this agent to the dispatcher, the user must run on their laptop if they
  haven't yet:
  `voice-dispatcher config add-agent <agent_id> --triggers "hey jarvis,jarvis" --voice <voice.onnx>`
- To re-pair a single agent (e.g. after a token rotation), run on the laptop:
  `voice-dispatcher config rotate-token <agent_id>`
  then re-run `/voice:configure` with the new pairing string. Other agents are
  unaffected (shared cert is unchanged).
- To re-pair all agents (after a cert rotation on the laptop), run:
  `voice-dispatcher tls rotate`
  then re-run `/voice:configure` on every agent container.
