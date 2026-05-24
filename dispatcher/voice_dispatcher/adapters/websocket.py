"""
WebSocket adapter (v1) — the only place that knows about WS frames.

Responsibilities:
  - Accept incoming agent connections
  - Verify token in the hello frame; close with 4001 on failure
  - Verify protocol version in hello; close with 4000 on mismatch
  - Parse inbound frames and call core handler functions
  - Subscribe to the core bus and push outbound events as WS frames
  - Respond to ping with pong

The core never touches websockets; this adapter never contains business logic.
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional

from ..core.handlers import Dispatcher
from ..core.models import TranscriptDispatched, PermissionVerdict
from ..core.session import AgentConfig, SessionRegistry

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1


class WebSocketAdapter:
    """
    Wraps a websockets server.  Call run() to serve indefinitely.

    All agent-config is read from the top-level config dict once at startup.
    """

    def __init__(self, dispatcher: Dispatcher, config: dict) -> None:
        self._dispatcher = dispatcher
        self._config = config

        # token → agent_id lookup table
        self._token_map: dict[str, str] = {}
        # agent_id → open websocket (one connection per agent)
        self._connections: dict[str, object] = {}

        # Register all configured agents in the session registry
        for agent_id, hcfg in config.get("agents", {}).items():
            token = hcfg.get("websocket_token", "")
            hc = AgentConfig(
                agent_id=agent_id,
                token=token,
                triggers=hcfg.get("triggers", []),
                language=hcfg.get("language"),
                voice=hcfg.get("voice", ""),
                enable_permission_relay=hcfg.get("enable_permission_relay", False),
            )
            self._dispatcher.registry.register(hc)
            self._token_map[token] = agent_id

        # Event loop — set when run() / an external harness starts the server.
        # Bus callbacks are called from arbitrary threads; they need the loop to
        # schedule coroutines safely.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Subscribe to events that need to be pushed to agents
        self._dispatcher.bus.subscribe(TranscriptDispatched, self._on_transcript_dispatched)
        self._dispatcher.bus.subscribe(PermissionVerdict, self._on_permission_verdict)

    async def run(self) -> None:
        """Start the WebSocket server and serve until cancelled."""
        try:
            import websockets  # type: ignore
        except ImportError:
            raise RuntimeError("websockets package not installed — run: pip install websockets")

        self._loop = asyncio.get_running_loop()

        server_cfg = self._config.get("server", {})
        host = server_cfg.get("host", "0.0.0.0")
        port = int(server_cfg.get("port", 7355))

        logger.info("WebSocket server listening on %s:%d", host, port)
        async with websockets.serve(self._handle_connection, host, port):
            await asyncio.Future()  # run forever

    # ── Per-connection handler ────────────────────────────────────────────────

    async def _handle_connection(self, ws) -> None:
        agent_id: Optional[str] = None
        try:
            # First message must be hello
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                await ws.close(4000, "hello timeout")
                return

            msg = json.loads(raw)

            # Protocol version check
            v = msg.get("v")
            if v != PROTOCOL_VERSION:
                await ws.close(4000, f"unsupported protocol version v{v}")
                logger.warning("rejected connection: protocol version %s", v)
                return

            # Token authentication
            token = msg.get("token", "")
            agent_id = self._token_map.get(token)
            if agent_id is None:
                await ws.close(4001, "authentication failed")
                logger.warning("rejected connection: invalid token")
                return

            logger.info("agent connected: %s", agent_id)
            self._connections[agent_id] = ws
            self._dispatcher.on_connected(agent_id)

            # Message loop
            async for raw in ws:
                await self._handle_message(agent_id, ws, raw)

        except Exception as exc:
            logger.debug("connection error: %s", exc)
        finally:
            if agent_id:
                self._connections.pop(agent_id, None)
                close_code = ws.close_code or 0
                close_reason = ws.close_reason or ""
                self._dispatcher.on_disconnected(agent_id, close_code, close_reason)
                logger.info("agent disconnected: %s (code=%s)", agent_id, close_code)

    async def _handle_message(self, agent_id: str, ws, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("bad JSON from %s", agent_id)
            return

        msg_type = msg.get("type")

        if msg_type == "speak":
            self._dispatcher.speak(
                agent_id=agent_id,
                utterance_id=msg.get("utterance_id", ""),
                text=msg.get("text", ""),
            )

        elif msg_type == "permission_request":
            self._dispatcher.request_permission(
                agent_id=agent_id,
                request_id=msg.get("request_id", ""),
                tool_name=msg.get("tool_name", ""),
                description=msg.get("description", ""),
                input_preview=msg.get("input_preview", ""),
            )

        elif msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))

        else:
            logger.debug("unknown message type from %s: %r", agent_id, msg_type)

    # ── Bus → WS fanout ───────────────────────────────────────────────────────

    def _schedule(self, coro) -> None:
        """Schedule a coroutine on the server's event loop from any thread."""
        if self._loop is None:
            logger.warning("_schedule: no event loop — message dropped")
            return
        self._loop.call_soon_threadsafe(self._loop.create_task, coro)

    def _on_transcript_dispatched(self, event: TranscriptDispatched) -> None:
        """Push a transcript frame to the agent's WebSocket."""
        ws = self._connections.get(event.agent_id)
        if ws is None:
            logger.warning("transcript dropped — agent %r not connected", event.agent_id)
            return
        payload = json.dumps({
            "type": "transcript",
            "utterance_id": event.utterance_id,
            "text": event.text,
            "lang": event.lang,
            "trigger": event.trigger,
            "ts": event.ts,
        })
        self._schedule(ws.send(payload))

    def _on_permission_verdict(self, event: PermissionVerdict) -> None:
        """Push a permission_verdict frame to the agent's WebSocket."""
        ws = self._connections.get(event.agent_id)
        if ws is None:
            return
        payload = json.dumps({
            "type": "permission_verdict",
            "request_id": event.request_id,
            "behavior": event.behavior,
        })
        self._schedule(ws.send(payload))
