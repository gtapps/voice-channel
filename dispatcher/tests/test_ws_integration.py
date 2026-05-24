"""
Milestone 3 acceptance check — transport-neutral core boundary.

Starts the real dispatcher core + WebSocket adapter (no audio pipeline).
Spawns the voice-channel plugin subprocess (Node.js).
Calls core.route_transcript() directly and asserts:
  - The plugin receives  notifications/claude/channel
  - The plugin can call  reply → dispatcher gets a speak frame
  - The core is independently drivable without any audio code.

Requires:  node (with tsx in plugin/voice/node_modules) and the plugin source.
           The test is auto-skipped if node is not available.
"""

from __future__ import annotations
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

from voice_dispatcher.core.handlers import Dispatcher
from voice_dispatcher.core.models import AgentConnected, SpeakRequest
from voice_dispatcher.core.session import AgentConfig
from voice_dispatcher.adapters.websocket import WebSocketAdapter

PLUGIN_ROOT = Path(__file__).parent.parent.parent / "plugin" / "voice"
NODE = "node"

# Skip if node binary isn't available or plugin deps aren't installed
pytestmark = pytest.mark.skipif(
    not (PLUGIN_ROOT / "node_modules" / ".bin" / "tsx").exists(),
    reason="plugin/voice/node_modules not installed — run npm install there first",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def frame(obj: dict) -> bytes:
    """MCP SDK newline-delimited JSON frame."""
    return (json.dumps(obj) + "\n").encode()


def parse_lines(data: bytes) -> list[dict]:
    """Parse newline-delimited JSON from bytes."""
    msgs = []
    for line in data.split(b"\n"):
        line = line.strip()
        if line:
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return msgs


# ── Fixture ───────────────────────────────────────────────────────────────────

class IntegrationHarness:
    """
    Runs the dispatcher core + WS adapter in a background asyncio thread,
    and manages a voice-channel plugin subprocess.
    """

    def __init__(self, port: int, data_dir: str, enable_permission_relay: bool = False):
        self.port = port
        self.data_dir = data_dir
        self.token = "integration-test-token"
        self.agent_id = "test-agent"

        self.dispatcher = Dispatcher()
        hc = AgentConfig(
            agent_id=self.agent_id,
            token=self.token,
            triggers=["hey jarvis"],
            language="en",
            voice="",
            enable_permission_relay=enable_permission_relay,
        )
        self.dispatcher.registry.register(hc)

        cfg = {
            "agents": {
                self.agent_id: {
                    "websocket_token": self.token,
                    "triggers": ["hey jarvis"],
                    "voice": "",
                    "enable_permission_relay": enable_permission_relay,
                }
            },
            "server": {"host": "127.0.0.1", "port": port},
        }
        self.adapter = WebSocketAdapter(self.dispatcher, cfg)

        # Events for synchronisation
        self._connected_event = threading.Event()
        self._speak_requests: list[SpeakRequest] = []
        self.dispatcher.bus.subscribe(AgentConnected, lambda e: self._connected_event.set())
        self.dispatcher.bus.subscribe(SpeakRequest, self._speak_requests.append)

        # Asyncio loop running in a daemon thread
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self._server_thread = threading.Thread(
            target=self._run_loop, name="ws-server", daemon=True
        )

        # Plugin subprocess
        self.proc: Optional[subprocess.Popen] = None
        self._stdout_buf = b""
        self._stdout_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None

    def start(self, timeout: float = 5.0) -> None:
        """Start the WS server and plugin subprocess, wait for connection."""
        self._server_thread.start()
        self._loop_ready.wait(timeout=timeout)

        # Write plugin config
        plugin_cfg = {
            "dispatcher_url": f"ws://127.0.0.1:{self.port}",
            "token": self.token,
            "agent_id": self.agent_id,
            "enable_permission_relay": False,
        }
        with open(os.path.join(self.data_dir, "config.json"), "w") as f:
            json.dump(plugin_cfg, f)

        # Spawn plugin
        self.proc = subprocess.Popen(
            [NODE, "--import", "tsx", str(PLUGIN_ROOT / "server.ts")],
            cwd=str(PLUGIN_ROOT),
            env={**os.environ, "CLAUDE_PLUGIN_DATA": self.data_dir, "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._reader_thread = threading.Thread(
            target=self._read_stdout, name="stdout-reader", daemon=True
        )
        self._reader_thread.start()

        # MCP initialize handshake
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "0"}}})
        init_resp = self._wait_for_id(1, timeout=10)
        assert init_resp is not None, "initialize timed out"
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # Wait for the plugin's WS client to connect and authenticate
        connected = self._connected_event.wait(timeout=10)
        assert connected, "plugin WS never connected to dispatcher"

    def stop(self) -> None:
        if self.proc:
            self.proc.stdin.close()  # type: ignore[union-attr]
            self.proc.kill()
            self.proc.wait(timeout=3)
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ── MCP I/O ──────────────────────────────────────────────────────────────

    def _send(self, obj: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(frame(obj))
        self.proc.stdin.flush()

    def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            chunk = self.proc.stdout.read1(256)  # type: ignore[attr-defined]
            if not chunk:
                break
            with self._stdout_lock:
                self._stdout_buf += chunk

    def _snapshot(self) -> bytes:
        with self._stdout_lock:
            return self._stdout_buf

    def _wait_for_id(self, req_id: int, timeout: float = 5.0) -> Optional[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in parse_lines(self._snapshot()):
                if msg.get("id") == req_id:
                    return msg
            time.sleep(0.05)
        return None

    def wait_for_notification(self, method: str, timeout: float = 5.0) -> Optional[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in parse_lines(self._snapshot()):
                if msg.get("method") == method and "id" not in msg:
                    return msg
            time.sleep(0.05)
        return None

    # ── Asyncio WS server in background thread ───────────────────────────────

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _serve() -> None:
            import websockets  # type: ignore
            # Capture the running loop so bus callbacks can schedule coroutines
            self.adapter._loop = asyncio.get_running_loop()
            async with websockets.serve(
                self.adapter._handle_connection,
                "127.0.0.1",
                self.port,
            ):
                self._loop_ready.set()
                await asyncio.Future()  # serve forever (until loop stops)

        try:
            self._loop.run_until_complete(_serve())
        except RuntimeError:
            pass  # loop stopped cleanly


@pytest.fixture
def harness():
    port = find_free_port()
    with tempfile.TemporaryDirectory(prefix="voice-int-test-") as tmpdir:
        h = IntegrationHarness(port, tmpdir)
        h.start()
        yield h
        h.stop()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_route_transcript_reaches_plugin(harness: IntegrationHarness) -> None:
    """
    Acceptance check: poke core.route_transcript() directly (no audio) →
    plugin receives notifications/claude/channel with the correct params.
    """
    harness.dispatcher.route_transcript(
        agent_id=harness.agent_id,
        utterance_id="u-int-001",
        text="turn on the lights",
        lang="en",
        trigger="hey jarvis",
        ts="2026-05-24T00:00:00Z",
    )

    notif = harness.wait_for_notification("notifications/claude/channel", timeout=5)
    assert notif is not None, "plugin never received notifications/claude/channel"

    params = notif["params"]
    assert params["content"] == "turn on the lights"
    assert params["meta"]["utterance_id"] == "u-int-001"
    assert params["meta"]["trigger"] == "hey jarvis"


def test_reply_tool_reaches_core(harness: IntegrationHarness) -> None:
    """
    Plugin calls the reply tool → dispatcher core emits SpeakRequest.
    """
    # First send a transcript so there's an utterance to reply to
    harness.dispatcher.route_transcript(
        agent_id=harness.agent_id,
        utterance_id="u-int-002",
        text="what time is it",
        lang="en",
        trigger="hey jarvis",
    )
    harness.wait_for_notification("notifications/claude/channel", timeout=5)

    # Simulate Claude calling the reply tool
    harness._send({
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": "reply", "arguments": {
            "utterance_id": "u-int-002",
            "text": "It is noon.",
        }},
    })

    # Wait for speak request on the bus
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if harness._speak_requests:
            break
        time.sleep(0.05)

    assert harness._speak_requests, "dispatcher never received SpeakRequest"
    sr = harness._speak_requests[0]
    assert sr.agent_id == harness.agent_id
    assert sr.utterance_id == "u-int-002"
    assert sr.text == "It is noon."
