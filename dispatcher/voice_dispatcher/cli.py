"""
CLI — `voice-dispatcher config` subcommands.

Usage:
    voice-dispatcher config add-hermit <id> --triggers "..." --voice <voice.onnx>
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
    """Manage hermit configuration."""


@config.command("add-hermit")
@click.argument("hermit_id")
@click.option("--triggers", required=True,
              help='Comma-separated trigger phrases, e.g. "hey jarvis,hermit"')
@click.option("--voice", required=True,
              help="Piper .onnx voice filename, e.g. en_US-lessac-medium.onnx")
@click.option("--language", default=None, help="Language hint (ISO-639-1), e.g. en")
@click.option("--token", default=None, help="Override token (default: auto-generated)")
def add_hermit(hermit_id: str, triggers: str, voice: str,
               language: Optional[str], token: Optional[str]) -> None:
    """Register a new hermit. Prints the token to paste into /voice:configure."""
    cfg = _load_config()
    hermits = cfg.setdefault("hermits", {})

    if hermit_id in hermits:
        click.echo(f"Warning: overwriting existing hermit {hermit_id!r}", err=True)

    tok = token or _generate_token()
    trigger_list = [t.strip() for t in triggers.split(",") if t.strip()]

    hermits[hermit_id] = {
        "triggers": trigger_list,
        "voice": voice,
        "websocket_token": tok,
    }
    if language:
        hermits[hermit_id]["language"] = language

    _save_config(cfg)

    click.echo(f"\n✓ Hermit {hermit_id!r} registered.")
    click.echo(f"  Token: {tok}")
    click.echo(f"\nNow inside that hermit's container, run:")
    click.echo(f"  /voice:configure")
    click.echo(f"  (use dispatcher URL and the token above)\n")


@config.command("list")
def list_hermits() -> None:
    """List registered hermits."""
    cfg = _load_config()
    hermits = cfg.get("hermits", {})
    if not hermits:
        click.echo("No hermits registered.")
        return
    for hid, hcfg in hermits.items():
        triggers = ", ".join(hcfg.get("triggers", []))
        voice = hcfg.get("voice", "(none)")
        click.echo(f"  {hid}  triggers=[{triggers}]  voice={voice}")


@config.command("rotate-token")
@click.argument("hermit_id")
def rotate_token(hermit_id: str) -> None:
    """Generate a new token for a hermit (invalidates the old one)."""
    cfg = _load_config()
    hermits = cfg.get("hermits", {})
    if hermit_id not in hermits:
        click.echo(f"Error: hermit {hermit_id!r} not found.", err=True)
        sys.exit(1)

    tok = _generate_token()
    hermits[hermit_id]["websocket_token"] = tok
    _save_config(cfg)

    click.echo(f"✓ New token for {hermit_id!r}: {tok}")
    click.echo(f"Re-run /voice:configure inside that hermit's container with the new token.")


@cli.command("list-devices")
def list_devices() -> None:
    """List available audio input/output devices."""
    try:
        import sounddevice as sd  # type: ignore
        click.echo(sd.query_devices())
    except ImportError:
        click.echo("sounddevice not installed — cannot list devices.", err=True)
        sys.exit(1)
