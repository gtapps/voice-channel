"""
TLS certificate helpers — cert generation, fingerprint, and auto-provisioning.

All paths are resolved at call time via config_dir() / cert_dir() — no
module-level constants — so that tests can vary VOICE_DISPATCHER_CONFIG_DIR
per-test without stale import-time captures.

Do NOT import cli.py here: cli imports tls (to call ensure()), so a back-import
is circular.  If a third consumer of config_dir() appears, extract it into a
tiny shared paths.py instead.
"""

from __future__ import annotations
import ipaddress
import os
import socket
from pathlib import Path


def config_dir() -> Path:
    """Return the dispatcher config directory, resolved at call time."""
    return Path(os.environ.get(
        "VOICE_DISPATCHER_CONFIG_DIR",
        os.path.expanduser("~/.config/voice-dispatcher"),
    ))


def cert_dir() -> Path:
    """Directory that holds dispatcher.crt / dispatcher.key."""
    return config_dir() / "tls"


def generate(force: bool = False) -> None:
    """
    Generate a self-signed RSA-2048 cert valid for ~10 years.
    No-op if both files exist and force=False.
    Key is written chmod 0600; cert is written chmod 0644.
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from datetime import datetime, timezone, timedelta

    d = cert_dir()
    crt_path = d / "dispatcher.crt"
    key_path = d / "dispatcher.key"

    if not force and crt_path.exists() and key_path.exists():
        return

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    hostname = socket.gethostname()
    now = datetime.now(timezone.utc)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName(hostname),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.IPAddress(ipaddress.IPv6Address("::1")),
            ]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    d.mkdir(parents=True, exist_ok=True)

    key_path.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    key_path.chmod(0o600)

    crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    crt_path.chmod(0o644)


def fingerprint_of(cert_path: Path) -> str:
    """
    Return the SHA-256 fingerprint of the cert at *cert_path* as uppercase
    colon-separated hex (e.g. 'AB:12:CD:...'), matching openssl output.
    Raises FileNotFoundError if the file does not exist.
    """
    from cryptography.x509 import load_pem_x509_certificate
    from cryptography.hazmat.primitives import hashes

    cert = load_pem_x509_certificate(cert_path.read_bytes())
    raw = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{b:02X}" for b in raw)


def fingerprint() -> str:
    """Return the fingerprint of the auto-provisioned dispatcher cert."""
    return fingerprint_of(cert_dir() / "dispatcher.crt")


def ensure() -> None:
    """Generate cert/key if either file is missing. Idempotent."""
    generate(force=False)
