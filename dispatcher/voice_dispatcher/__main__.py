"""
voice-dispatcher entrypoint.

Usage:
    python -m voice_dispatcher [--config path] [--no-adapter]

    --no-adapter   Start the audio pipeline and core only (no WebSocket server).
                   Useful for standalone audio testing: milestone 2 verification.
"""

from __future__ import annotations
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import click
import yaml

from .cli import cli  # re-export CLI subcommands
from .core.handlers import Dispatcher
from . import tls as _tls
from .adapters.websocket import _tls_enabled


DEFAULT_CONFIG = Path(os.environ.get(
    "VOICE_DISPATCHER_CONFIG_DIR",
    os.path.expanduser("~/.config/voice-dispatcher"),
)) / "config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("voice_dispatcher")


def _load_config(path: Path) -> dict:
    if not path.exists():
        logger.error("Config not found: %s\nRun: voice-dispatcher config add-agent", path)
        sys.exit(1)
    with open(path) as f:
        return yaml.safe_load(f) or {}


@cli.command("run")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG),
              show_default=True, help="Path to config.yaml")
@click.option("--no-adapter", is_flag=True,
              help="Skip WebSocket server (audio + core only; for standalone testing)")
def run_server(config_path: str, no_adapter: bool) -> None:
    """Start voice-dispatcher."""
    cfg = _load_config(Path(config_path))
    dispatcher = Dispatcher()

    # Audio pipeline
    from .audio.pipeline import AudioPipeline
    pipeline = AudioPipeline(dispatcher, cfg)

    async def _main() -> None:
        # Pre-register agents from config so route_transcript works in --no-adapter mode
        # (normally the WS adapter does this on hello handshake)
        from .core.session import AgentConfig
        for agent_id, agent_cfg in cfg.get("agents", {}).items():
            dispatcher.registry.register(AgentConfig(
                agent_id=agent_id,
                token=agent_cfg.get("websocket_token", ""),
                triggers=agent_cfg.get("triggers", []),
                language=agent_cfg.get("language"),
                voice=agent_cfg.get("voice", ""),
            ))

        pipeline.start()
        try:
            if no_adapter:
                logger.info("Running without WebSocket adapter (audio+core only)")
                # Block until SIGINT/SIGTERM
                loop = asyncio.get_running_loop()
                stop = asyncio.Event()
                loop.add_signal_handler(signal.SIGINT, stop.set)
                loop.add_signal_handler(signal.SIGTERM, stop.set)
                await stop.wait()
            else:
                # Auto-provision TLS cert before the adapter tries to load it.
                if _tls_enabled(cfg):
                    _tls.ensure()
                from .adapters.websocket import WebSocketAdapter
                adapter = WebSocketAdapter(dispatcher, cfg)
                await adapter.run()
        finally:
            pipeline.stop()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass


# Make `python -m voice_dispatcher` work; also wire up `voice-dispatcher` script
def main() -> None:
    cli()


if __name__ == "__main__":
    main()
