"""Fake remote PlexiChat instance for self-test federation delivery.

Runs a minimal HTTPS server on 127.0.0.1 alongside the self-test suite and
answers the outbound federation surface (join-request / message) with valid
data. This lets ``PlexiJoinManager._post_signed`` run its REAL code path —
SSRF validation, TLS verification, HMAC signing, httpx delivery, traffic
accounting — against a live peer instead of the public internet.

TLS trust (no production impact): the instance mints a throwaway CA, serves
a leaf cert signed by it, and publishes a trust bundle (system CAs + fake
CA) via the ``SSL_CERT_FILE`` process env var while it runs. Delivery
clients therefore perform REAL certificate verification against a chain
that includes the fake CA; public HTTPS keeps working because the system
bundle is included. The env var is restored on stop. Outbound delivery to
loopback is permitted by the ``PLEXICHAT_SELF_TEST``-gated allowance in
``URLValidator``, which never applies in production.

The server only binds loopback and only answers while the suite runs it;
it is never part of production.
"""

from __future__ import annotations

import datetime
import ipaddress
import json
import logging
import os
import ssl
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _federation_paths() -> tuple[str, str]:
    """Outbound delivery paths (mirror PlexiJoinManager constants)."""
    try:
        from src.core.plexijoin.manager import FEDERATION_JOIN_REQUEST_PATH
        from src.core.plexijoin.manager import FEDERATION_MESSAGE_PATH

        return FEDERATION_JOIN_REQUEST_PATH, FEDERATION_MESSAGE_PATH
    except Exception:
        return "/api/v1/federation/join-request", "/api/v1/federation/message"


JOIN_REQUEST_PATH, MESSAGE_PATH = _federation_paths()


def _generate_ca_and_leaf(ca_path: Path, cert_path: Path, key_path: Path) -> None:
    """Mint a throwaway CA + leaf cert for 127.0.0.1 (test use only)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-remote-ca")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.DNSName("localhost"),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    ca_path.write_bytes(ca_pem)
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))


def _build_trust_bundle(ca_pem: bytes, bundle_path: Path) -> None:
    """System CA bundle + fake CA, so public HTTPS keeps working."""
    try:
        import certifi

        with open(certifi.where(), "rb") as f:
            system_bundle = f.read()
    except Exception:
        system_bundle = b""
    with open(bundle_path, "wb") as f:
        f.write(system_bundle)
        if system_bundle and not system_bundle.endswith(b"\n"):
            f.write(b"\n")
        f.write(ca_pem)


class _FakeRemoteHandler(BaseHTTPRequestHandler):
    """Serve the federation surface with valid, recorded responses."""

    server_version = "FakePlexiChat/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # keep suite output clean
        return

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(min(length, 1024 * 1024))

    def _record(self, body: bytes) -> None:
        try:
            parsed: Any = json.loads(body.decode("utf-8")) if body else None
        except Exception:
            parsed = None
        record = {
            "method": self.command,
            "path": self.path.split("?", 1)[0],
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
            "json": parsed,
            "at": time.time(),
        }
        server = self.server
        assert isinstance(server, _FakeRemoteServer)
        with server.records_lock:
            server.records.append(record)

    def do_POST(self) -> None:  # noqa: N802
        body = self._read_body()
        path = self.path.split("?", 1)[0]
        self._record(body)
        if path == JOIN_REQUEST_PATH:
            self._send_json(
                201,
                {
                    "success": True,
                    "status": "pending",
                    "instance_id": "fake-remote-selftest",
                    "message": "Join request recorded",
                },
            )
        elif path == MESSAGE_PATH:
            self._send_json(
                200,
                {
                    "success": True,
                    "delivered": True,
                    "instance_id": "fake-remote-selftest",
                },
            )
        else:
            self._send_json(404, {"success": False, "error": "unknown federation path"})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/api/v1/health", "/health"):
            self._send_json(
                200, {"status": "ok", "instance_id": "fake-remote-selftest"}
            )
        else:
            self._send_json(404, {"success": False, "error": "unknown federation path"})


class _FakeRemoteServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.records: List[Dict[str, Any]] = []
        self.records_lock = threading.Lock()


class FakeRemoteInstance:
    """A live fake peer answering federation delivery with valid data."""

    def __init__(self) -> None:
        self._server: Optional[_FakeRemoteServer] = None
        self._thread: Optional[threading.Thread] = None
        self._tmpdir: Optional[tempfile.TemporaryDirectory[str]] = None
        self._prev_ssl_cert_file: Optional[str] = None
        self._trust_bundle = ""
        self.base_url = ""

    @property
    def received(self) -> List[Dict[str, Any]]:
        if self._server is None:
            return []
        with self._server.records_lock:
            return list(self._server.records)

    def received_for(self, path: str) -> List[Dict[str, Any]]:
        return [r for r in self.received if r["path"] == path]

    def start(self, timeout: float = 10.0) -> str:
        """Bind 127.0.0.1:0 with TLS, serve in a daemon thread, wait ready."""
        logger.warning(
            "SELF-TEST ONLY: starting fake remote federation peer over "
            "HTTPS on loopback with a throwaway CA. Trust is published via "
            "SSL_CERT_FILE for this process only and restored on stop. "
            "Never used in production."
        )
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fake-remote-")
        tmp = Path(self._tmpdir.name)
        ca_path, cert_path, key_path = (
            tmp / "ca.pem",
            tmp / "cert.pem",
            tmp / "key.pem",
        )
        _generate_ca_and_leaf(ca_path, cert_path, key_path)
        bundle_path = tmp / "bundle.pem"
        _build_trust_bundle(ca_path.read_bytes(), bundle_path)
        self._trust_bundle = str(bundle_path)
        self._prev_ssl_cert_file = os.environ.get("SSL_CERT_FILE")
        os.environ["SSL_CERT_FILE"] = self._trust_bundle

        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert_path), str(key_path))

            self._server = _FakeRemoteServer(("127.0.0.1", 0), _FakeRemoteHandler)
            self._server.socket = context.wrap_socket(
                self._server.socket, server_side=True
            )
        except Exception:
            self.stop()
            raise
        port = self._server.server_address[1]
        self.base_url = f"https://127.0.0.1:{port}"

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
            name="fake-remote-federation",
        )
        self._thread.start()

        import requests

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                # Verify against our bundle: proves the chain validates.
                resp = requests.get(
                    self.base_url + "/api/v1/health",
                    timeout=1,
                    verify=self._trust_bundle,
                )
                if resp.status_code == 200:
                    return self.base_url
            except Exception:
                pass
            time.sleep(0.05)
        self.stop()
        raise RuntimeError("Fake remote instance did not become ready")

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        # Restore process TLS trust to exactly what it was.
        try:
            if self._prev_ssl_cert_file is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = self._prev_ssl_cert_file
        except Exception:
            pass
        self._prev_ssl_cert_file = None
        self._trust_bundle = ""
        if self._tmpdir is not None:
            try:
                self._tmpdir.cleanup()
            except Exception:
                pass
            self._tmpdir = None
        self.base_url = ""
