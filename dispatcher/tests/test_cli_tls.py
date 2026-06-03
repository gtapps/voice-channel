"""
CLI tests for TLS cert management and pairing string generation.

Each test uses an isolated VOICE_DISPATCHER_CONFIG_DIR via CliRunner env overrides,
relying on tls.config_dir() resolving at call time (not import time).
"""

from __future__ import annotations
import base64
import json
import os
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from voice_dispatcher.cli import cli
from voice_dispatcher import tls as _tls


@pytest.fixture
def runner_env(tmp_path: Path):
    """CliRunner + env with VOICE_DISPATCHER_CONFIG_DIR pointing to a temp dir."""
    runner = CliRunner()
    env = {**os.environ, "VOICE_DISPATCHER_CONFIG_DIR": str(tmp_path)}
    return runner, env, tmp_path


# ── tls fingerprint ───────────────────────────────────────────────────────────

def test_tls_fingerprint_auto_provisions(runner_env):
    """tls fingerprint auto-generates the cert if missing and prints a fingerprint."""
    runner, env, tmp_path = runner_env
    result = runner.invoke(cli, ["tls", "fingerprint"], env=env)
    assert result.exit_code == 0, result.output
    fp = result.output.strip()
    # Should be 95 chars: 32 bytes × 2 hex + 31 colons
    assert len(fp) == 95
    assert ":" in fp


def test_tls_fingerprint_cert_is_chmod_0600(runner_env):
    """The private key file must be restricted."""
    runner, env, tmp_path = runner_env
    runner.invoke(cli, ["tls", "fingerprint"], env=env)
    key = tmp_path / "tls" / "dispatcher.key"
    assert key.exists()
    assert oct(key.stat().st_mode & 0o777) == oct(0o600)


def test_tls_fingerprint_idempotent(runner_env):
    """Running fingerprint twice returns the same value."""
    runner, env, _ = runner_env
    r1 = runner.invoke(cli, ["tls", "fingerprint"], env=env)
    r2 = runner.invoke(cli, ["tls", "fingerprint"], env=env)
    assert r1.exit_code == 0 and r2.exit_code == 0
    assert r1.output.strip() == r2.output.strip()


# ── tls rotate ────────────────────────────────────────────────────────────────

def test_tls_rotate_changes_fingerprint(runner_env):
    """tls rotate replaces the cert so the fingerprint changes."""
    runner, env, _ = runner_env
    r1 = runner.invoke(cli, ["tls", "fingerprint"], env=env)
    r2 = runner.invoke(cli, ["tls", "rotate"], env=env)
    r3 = runner.invoke(cli, ["tls", "fingerprint"], env=env)
    assert r1.exit_code == 0 and r2.exit_code == 0 and r3.exit_code == 0
    assert r1.output.strip() != r3.output.strip()


def test_tls_rotate_warns_about_re_pairing(runner_env):
    runner, env, _ = runner_env
    runner.invoke(cli, ["tls", "fingerprint"], env=env)  # provision first
    result = runner.invoke(cli, ["tls", "rotate"], env=env)
    assert result.exit_code == 0
    assert "re-pair" in result.output.lower()


# ── add-agent pairing string ──────────────────────────────────────────────────

def _decode_pairing(pair: str) -> dict:
    """Decode a voicepair_<base64url> string back to its JSON payload."""
    assert pair.startswith("voicepair_"), f"bad prefix: {pair!r}"
    b64 = pair[len("voicepair_"):]
    # urlsafe_b64decode tolerates missing padding
    padding = 4 - len(b64) % 4
    if padding != 4:
        b64 += "=" * padding
    return json.loads(base64.urlsafe_b64decode(b64))


def test_add_agent_emits_pairing_string(runner_env):
    """add-agent should print a v2 voicepair_... string with agent, token, and cert."""
    runner, env, tmp_path = runner_env
    result = runner.invoke(
        cli,
        ["config", "add-agent", "jarvis",
         "--triggers", "hey jarvis,jarvis",
         "--voice", "en_US-lessac-medium.onnx"],
        env=env,
    )
    assert result.exit_code == 0, result.output

    # Extract the pairing string from output
    pair = None
    for line in result.output.splitlines():
        line = line.strip()
        if line.startswith("voicepair_"):
            pair = line
            break
    assert pair is not None, f"no voicepair_ in output:\n{result.output}"

    payload = _decode_pairing(pair)
    assert payload["pairing_v"] == 2
    assert payload["agent_id"] == "jarvis"
    assert isinstance(payload["token"], str) and len(payload["token"]) > 10
    assert isinstance(payload["cert_sha256"], str)
    assert payload["cert_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert payload["cert_pem"] == (tmp_path / "tls" / "dispatcher.crt").read_text()


def test_add_agent_cert_sha256_matches_tls_fingerprint(runner_env):
    """The pairing cert_pem and cert_sha256 must match the served TLS cert."""
    runner, env, tmp_path = runner_env
    r_add = runner.invoke(
        cli,
        ["config", "add-agent", "jarvis",
         "--triggers", "hey jarvis",
         "--voice", "en_US-lessac-medium.onnx"],
        env=env,
    )
    assert r_add.exit_code == 0

    pair = next(
        (l.strip() for l in r_add.output.splitlines() if l.strip().startswith("voicepair_")),
        None,
    )
    assert pair is not None
    payload = _decode_pairing(pair)

    r_fp = runner.invoke(cli, ["tls", "fingerprint"], env=env)
    assert r_fp.exit_code == 0
    # payload cert_sha256 is colon-separated; fingerprint output is also colon-separated
    assert payload["cert_sha256"] == r_fp.output.strip()
    assert payload["cert_pem"] == (tmp_path / "tls" / "dispatcher.crt").read_text()


# ── rotate-token pairing string ───────────────────────────────────────────────

def test_rotate_token_emits_pairing_string(runner_env):
    """rotate-token should emit a fresh voicepair_... for that agent."""
    runner, env, _ = runner_env
    # Register the agent first
    runner.invoke(
        cli,
        ["config", "add-agent", "jarvis",
         "--triggers", "hey jarvis",
         "--voice", "en_US-lessac-medium.onnx"],
        env=env,
    )
    result = runner.invoke(cli, ["config", "rotate-token", "jarvis"], env=env)
    assert result.exit_code == 0, result.output

    pair = next(
        (l.strip() for l in result.output.splitlines() if l.strip().startswith("voicepair_")),
        None,
    )
    assert pair is not None, f"no voicepair_ in output:\n{result.output}"
    payload = _decode_pairing(pair)
    assert payload["pairing_v"] == 2
    assert payload["agent_id"] == "jarvis"
    assert payload["cert_pem"].startswith("-----BEGIN CERTIFICATE-----")


# ── set-language ──────────────────────────────────────────────────────────────

def _read_config(tmp_path: Path) -> dict:
    return yaml.safe_load((tmp_path / "config.yaml").read_text())


def _add_jarvis(runner, env) -> None:
    runner.invoke(
        cli,
        ["config", "add-agent", "jarvis",
         "--triggers", "hey jarvis",
         "--voice", "en_US-lessac-medium.onnx"],
        env=env,
    )


def test_set_language_global(runner_env):
    """set-language without --agent writes the global whisper.language lock."""
    runner, env, tmp_path = runner_env
    result = runner.invoke(cli, ["config", "set-language", "pt"], env=env)
    assert result.exit_code == 0, result.output
    assert _read_config(tmp_path)["whisper"]["language"] == "pt"


def test_set_language_global_clear(runner_env):
    """'auto' clears the global lock to None (auto-detect)."""
    runner, env, tmp_path = runner_env
    runner.invoke(cli, ["config", "set-language", "pt"], env=env)
    runner.invoke(cli, ["config", "set-language", "auto"], env=env)
    assert _read_config(tmp_path)["whisper"]["language"] is None


def test_set_language_agent(runner_env):
    """--agent sets that agent's language."""
    runner, env, tmp_path = runner_env
    _add_jarvis(runner, env)
    result = runner.invoke(
        cli, ["config", "set-language", "pt", "--agent", "jarvis"], env=env)
    assert result.exit_code == 0, result.output
    assert _read_config(tmp_path)["agents"]["jarvis"]["language"] == "pt"


def test_set_language_agent_clear(runner_env):
    """'auto' --agent removes the agent's language key."""
    runner, env, tmp_path = runner_env
    _add_jarvis(runner, env)
    runner.invoke(cli, ["config", "set-language", "pt", "--agent", "jarvis"], env=env)
    runner.invoke(cli, ["config", "set-language", "auto", "--agent", "jarvis"], env=env)
    assert "language" not in _read_config(tmp_path)["agents"]["jarvis"]


def test_set_language_agent_not_found(runner_env):
    """--agent on a missing agent errors out."""
    runner, env, _ = runner_env
    result = runner.invoke(
        cli, ["config", "set-language", "pt", "--agent", "ghost"], env=env)
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_set_language_lowercased(runner_env):
    """Language codes are normalized to lowercase on write."""
    runner, env, tmp_path = runner_env
    runner.invoke(cli, ["config", "set-language", "PT"], env=env)
    assert _read_config(tmp_path)["whisper"]["language"] == "pt"
