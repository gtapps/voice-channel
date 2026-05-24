"""
CLI — `voice-dispatcher config` subcommands.

Usage:
    voice-dispatcher config add-agent <id> --triggers "..." --voice <voice.onnx>
    voice-dispatcher config list
    voice-dispatcher config rotate-token <id>
    voice-dispatcher list-devices
"""

from __future__ import annotations
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

import click
import yaml


CONFIG_DIR = Path(os.environ.get("VOICE_DISPATCHER_CONFIG_DIR",
                                  os.path.expanduser("~/.config/voice-dispatcher")))
CONFIG_FILE = CONFIG_DIR / "config.yaml"


def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {}


def _save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


# ── CLI entry-point ───────────────────────────────────────────────────────────

@click.group()
def cli() -> None:
    """voice-dispatcher — ambient voice control for claude-code-hermit."""


@cli.group()
def config() -> None:
    """Manage agent configuration."""


@config.command("add-agent")
@click.argument("agent_id")
@click.option("--triggers", required=True,
              help='Comma-separated trigger phrases, e.g. "hey jarvis,agent"')
@click.option("--voice", required=True,
              help="Piper .onnx voice filename, e.g. en_US-lessac-medium.onnx")
@click.option("--language", default=None, help="Language hint (ISO-639-1), e.g. en")
@click.option("--token", default=None, help="Override token (default: auto-generated)")
def add_agent(agent_id: str, triggers: str, voice: str,
               language: Optional[str], token: Optional[str]) -> None:
    """Register a new agent. Prints the token to paste into /voice:configure."""
    cfg = _load_config()
    agents = cfg.setdefault("agents", {})

    if agent_id in agents:
        click.echo(f"Warning: overwriting existing agent {agent_id!r}", err=True)

    tok = token or _generate_token()
    trigger_list = [t.strip() for t in triggers.split(",") if t.strip()]

    agents[agent_id] = {
        "triggers": trigger_list,
        "voice": voice,
        "websocket_token": tok,
    }
    if language:
        agents[agent_id]["language"] = language

    _save_config(cfg)

    click.echo(f"\n✓ Agent {agent_id!r} registered.")
    click.echo(f"  Token: {tok}")
    click.echo(f"\nNow inside that agent's container, run:")
    click.echo(f"  /voice:configure")
    click.echo(f"  (use dispatcher URL and the token above)\n")


@config.command("list")
def list_agents() -> None:
    """List registered agents."""
    cfg = _load_config()
    agents = cfg.get("agents", {})
    if not agents:
        click.echo("No agents registered.")
        return
    for hid, hcfg in agents.items():
        triggers = ", ".join(hcfg.get("triggers", []))
        voice = hcfg.get("voice", "(none)")
        click.echo(f"  {hid}  triggers=[{triggers}]  voice={voice}")


@config.command("rotate-token")
@click.argument("agent_id")
def rotate_token(agent_id: str) -> None:
    """Generate a new token for an agent (invalidates the old one)."""
    cfg = _load_config()
    agents = cfg.get("agents", {})
    if agent_id not in agents:
        click.echo(f"Error: agent {agent_id!r} not found.", err=True)
        sys.exit(1)

    tok = _generate_token()
    agents[agent_id]["websocket_token"] = tok
    _save_config(cfg)

    click.echo(f"✓ New token for {agent_id!r}: {tok}")
    click.echo(f"Re-run /voice:configure inside that agent's container with the new token.")


@cli.command("list-devices")
def list_devices() -> None:
    """List available audio input/output devices."""
    try:
        import sounddevice as sd  # type: ignore
        click.echo(sd.query_devices())
    except ImportError:
        click.echo("sounddevice not installed — cannot list devices.", err=True)
        sys.exit(1)
