"""
CLI — `voice-dispatcher config` subcommands.

Usage:
    voice-dispatcher config add-agent <id> --triggers "..." --voice <voice.onnx>
    voice-dispatcher config list
    voice-dispatcher config rotate-token <id>
    voice-dispatcher config set-language <lang> [--agent <id>]
    voice-dispatcher list-devices
"""

from __future__ import annotations
import base64
import json
import secrets
import sys
from pathlib import Path
from typing import Optional

import click
import yaml

from . import tls as _tls


def _config_file() -> Path:
    """Path to config.yaml, resolved at call time (mirrors tls.config_dir())."""
    return _tls.config_dir() / "config.yaml"


def _load_config() -> dict:
    path = _config_file()
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_config(cfg: dict) -> None:
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _cert_file_for_config(cfg: dict) -> Path:
    """Path to the cert the adapter serves (server.tls.cert_file if set, else the auto-provisioned default)."""
    cert_file_str = cfg.get("server", {}).get("tls", {}).get("cert_file")
    return Path(cert_file_str).expanduser() if cert_file_str else _tls.cert_dir() / "dispatcher.crt"


def _cert_sha256_for_config(cfg: dict) -> str:
    return _tls.fingerprint_of(_cert_file_for_config(cfg))


def _cert_pem_for_config(cfg: dict) -> str:
    return _cert_file_for_config(cfg).read_text()


def _pairing_string(agent_id: str, token: str, cert_sha256: str, cert_pem: str) -> str:
    """
    Encode agent_id + token + dispatcher cert into a single paste-safe string.
    Decoded by the plugin's /voice:configure skill via Bun's Buffer.from(..., 'base64url').
    """
    payload = json.dumps({
        "pairing_v": 2,
        "agent_id": agent_id,
        "token": token,
        "cert_sha256": cert_sha256,
        "cert_pem": cert_pem,
    }, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"voicepair_{encoded}"


# ── CLI entry-point ───────────────────────────────────────────────────────────

@click.group()
def cli() -> None:
    """voice-dispatcher — ambient voice control for Claude Code."""


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
    """Register a new agent and print its pairing string for /voice:configure."""
    # Auto-provision TLS cert if missing (so the pairing string can embed the cert).
    _tls.ensure()

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

    pair = _pairing_string(agent_id, tok, _cert_sha256_for_config(cfg), _cert_pem_for_config(cfg))
    click.echo(f"\n✓ Agent {agent_id!r} registered.")
    click.echo(f"\nPairing string (paste into /voice:configure):")
    click.echo(f"  {pair}")
    click.echo(f"\n  (token: {tok})")
    click.echo(f"\nInside that agent's container, run /voice:configure\n")


@config.command("list")
def list_agents() -> None:
    """List registered agents."""
    cfg = _load_config()
    global_lang = cfg.get("whisper", {}).get("language")
    if global_lang:
        click.echo(f"global whisper language lock: {global_lang}")
    agents = cfg.get("agents", {})
    if not agents:
        click.echo("No agents registered.")
        return
    for hid, hcfg in agents.items():
        triggers = ", ".join(hcfg.get("triggers", []))
        voice = hcfg.get("voice", "(none)")
        lang = hcfg.get("language")
        suffix = f"  lang={lang}" if lang else ""
        click.echo(f"  {hid}  triggers=[{triggers}]  voice={voice}{suffix}")


@config.command("rotate-token")
@click.argument("agent_id")
def rotate_token(agent_id: str) -> None:
    """Generate a new token for an agent (invalidates the old one)."""
    # Auto-provision TLS cert if missing so the pairing string can embed the cert.
    _tls.ensure()

    cfg = _load_config()
    agents = cfg.get("agents", {})
    if agent_id not in agents:
        click.echo(f"Error: agent {agent_id!r} not found.", err=True)
        sys.exit(1)

    tok = _generate_token()
    agents[agent_id]["websocket_token"] = tok
    _save_config(cfg)

    pair = _pairing_string(agent_id, tok, _cert_sha256_for_config(cfg), _cert_pem_for_config(cfg))
    click.echo(f"✓ Token rotated for {agent_id!r}.")
    click.echo(f"\nNew pairing string (paste into /voice:configure — this agent only):")
    click.echo(f"  {pair}")
    click.echo(f"\n  (token: {tok})")
    click.echo(f"\nOther agents are unaffected (shared cert unchanged).\n")


@config.command("remove-agent")
@click.argument("agent_id")
def remove_agent(agent_id: str) -> None:
    """Remove a registered agent."""
    cfg = _load_config()
    agents = cfg.get("agents", {})
    if agent_id not in agents:
        click.echo(f"Error: agent {agent_id!r} not found.", err=True)
        sys.exit(1)
    del agents[agent_id]
    _save_config(cfg)
    click.echo(f"✓ Agent {agent_id!r} removed.")


_CLEAR_SENTINELS = {"auto", "none", "null"}


@config.command("set-language")
@click.argument("language")
@click.option("--agent", "agent_id", default=None,
              help="Set this agent's language instead of the global lock.")
def set_language(language: str, agent_id: Optional[str]) -> None:
    """Set the Whisper decode language (ISO-639-1, e.g. pt). Use 'auto' to clear."""
    cfg = _load_config()
    clearing = language.lower() in _CLEAR_SENTINELS
    lang = None if clearing else language.lower()

    if agent_id is not None:
        agents = cfg.get("agents", {})
        if agent_id not in agents:
            click.echo(f"Error: agent {agent_id!r} not found.", err=True)
            sys.exit(1)
        if clearing:
            agents[agent_id].pop("language", None)
        else:
            agents[agent_id]["language"] = lang
        target = f"agent {agent_id!r}"
    else:
        cfg.setdefault("whisper", {})["language"] = lang
        target = "global whisper lock"

    _save_config(cfg)
    shown = "auto-detect" if clearing else lang
    click.echo(f"✓ Language for {target} set to {shown}.")
    click.echo("  Restart the dispatcher for it to take effect.")


@cli.command("list-devices")
def list_devices() -> None:
    """List available audio input/output devices."""
    try:
        import sounddevice as sd  # type: ignore
        click.echo(sd.query_devices())
    except ImportError:
        click.echo("sounddevice not installed — cannot list devices.", err=True)
        sys.exit(1)


# ── TLS group ─────────────────────────────────────────────────────────────────

@cli.group()
def tls() -> None:
    """Manage the dispatcher TLS certificate."""


@tls.command("fingerprint")
def tls_fingerprint() -> None:
    """Print the SHA-256 fingerprint of the dispatcher cert (auto-provisions if missing)."""
    _tls.ensure()
    fp = _tls.fingerprint()
    click.echo(fp)


@tls.command("rotate")
def tls_rotate() -> None:
    """Replace the dispatcher cert/key and print the new fingerprint.

    All agents must be re-paired after rotation (run 'config rotate-token <id>'
    for each agent — that prints a fresh pairing string with the new cert hash).
    """
    _tls.generate(force=True)
    fp = _tls.fingerprint()
    click.echo(f"✓ New cert fingerprint: {fp}")
    click.echo("⚠ All agents must re-pair: run 'voice-dispatcher config rotate-token <id>' for each.")
