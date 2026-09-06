"""Fake remote PlexiChat instance for self-test federation delivery.

Runs a minimal plain-HTTP server on 127.0.0.1 alongside the self-test suite
and answers the outbound federation surface (join-request / message) with
valid data. This lets ``PlexiJoinManager._post_signed`` run its REAL code
path — SSRF validation, HMAC signing, httpx delivery, traffic accounting —
against a live peer instead of the public internet.

WARNING: plain HTTP on loopback, self-test only. Outbound delivery to it is
permitted by test-gated allowances (``PLEXICHAT_SELF_TEST``) in
``validate_remote_url``/``URLValidator`` which never apply in production.
The server only binds loopback and only answers while the suite runs it;
it is never part of production.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
        """Bind 127.0.0.1:0 (plain HTTP), serve in a daemon thread, wait ready."""
        logger.warning(
            "SELF-TEST ONLY: starting fake remote federation peer over "
            "plain HTTP on loopback. Never used in production."
        )
        self._server = _FakeRemoteServer(("127.0.0.1", 0), _FakeRemoteHandler)
        port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{port}"

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
                resp = requests.get(
                    self.base_url + "/api/v1/health",
                    timeout=1,
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
        self.base_url = ""
