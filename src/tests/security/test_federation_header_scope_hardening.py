"""Regression tests for two PlexiJoin federation boundary gaps.

* The inbound federation routes are unauthenticated by JWT: the ``x-plexijoin-*``
  headers are the only credential. ``_authenticate`` fed the raw header strings
  straight into ``hmac.compare_digest`` and ``str.encode('ascii')``, so a
  non-ASCII signature/timestamp/nonce raised ``TypeError``/``UnicodeEncodeError``
  and produced a 500 on a public endpoint instead of a clean 401. Worse, any
  non-canonical header value reached the HMAC material and the replay-nonce key
  without a shape check. Strict header validation now fails closed.

* The ``list_requests`` / ``get_status`` / ``get_traffic`` admin endpoints only
  called ``get_admin_from_token`` (authentication), unlike every other
  PlexiJoin admin route which calls ``require_admin_permission`` with the
  ``admin.plexijoin.manage`` scope. Any authenticated admin could therefore read
  inbound federation requests (remote URLs, notes, requesting peers) and
  federation traffic/status without the federation permission.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from src.api.routes import federation as mod
from src.core.artifacts.federation import _sign_payload

SCOPE = "admin.plexijoin.manage"


# --- 1. Strict x-plexijoin-* header validation -----------------------------


class _Manager:
    """Minimal manager double: one connection, always-fresh replay nonce."""

    def __init__(self, shared_key: str = "k" * 32) -> None:
        self.shared_key = shared_key
        self.nonce_calls = []

    def list_connections(self, status=None):  # noqa: D401 - test double
        return {"connections": [{"id": 1, "shared_key": self.shared_key}]}

    def consume_replay_nonce(self, connection_id, nonce, timestamp):
        self.nonce_calls.append((connection_id, nonce, timestamp))
        return True


def _headers(body: bytes, *, ts: str = "1700000000", nonce: str = "n0nce"):
    signature = _sign_payload("k" * 32, body, ts, nonce)
    return signature, ts, nonce


def test_authenticate_accepts_a_well_signed_envelope():
    """Legitimate canonical headers still authenticate (no over-correction)."""
    body = b'{"message_type": "ping"}'
    sig, ts, nonce = _headers(body)
    mgr = _Manager()
    assert mod._authenticate(mgr, body, sig, ts, nonce) == {
        "id": 1,
        "shared_key": "k" * 32,
    }
    assert mgr.nonce_calls == [(1, nonce, 1700000000)]


@pytest.mark.parametrize(
    "signature, timestamp, nonce",
    [
        # Non-ASCII header values used to raise TypeError/UnicodeEncodeError in
        # compare_digest / .encode('ascii') and surfaced as a 500.
        ("\u00e9" * 64, "1700000000", "n0nce"),
        ("a" * 64, "17\u00e900000000", "n0nce"),
        ("a" * 64, "1700000000", "no\u00e9nce"),
        # Purely non-canonical (but ASCII) values must also fail closed so the
        # HMAC material and replay key have one unambiguous representation.
        ("zz" * 32, "1700000000", "n0nce"),
        ("A" * 64, "1700000000", "n0nce"),  # uppercase hex is not the digest
        ("a" * 63, "1700000000", "n0nce"),  # short digest
        ("a" * 64, "00017000000000", "n0nce"),  # leading zero timestamp
        ("a" * 64, "-1", "n0nce"),
        ("a" * 64, "1_700_000_000", "n0nce"),
        ("a" * 64, "1700000000", "bad nonce!"),
        ("a" * 64, "1700000000", "x" * 257),
    ],
)
def test_authenticate_rejects_malformed_headers_without_raising(
    signature: str, timestamp: str, nonce: str
):
    """A malformed signature envelope is a 401, never a 500 or an auth bypass."""
    body = b'{"message_type": "ping"}'
    mgr = _Manager()
    result = mod._authenticate(mgr, body, signature, timestamp, nonce)
    assert result is None
    assert mgr.nonce_calls == [], "no replay nonce may be claimed for a bad envelope"


def test_authenticate_still_rejects_a_wrong_signature():
    """Shape-valid but incorrect signatures remain unauthenticated."""
    body = b'{"message_type": "ping"}'
    _, ts, nonce = _headers(body)
    assert mod._authenticate(_Manager(), body, "0" * 64, ts, nonce) is None


# --- 2. Scope gate on PlexiJoin admin read endpoints -----------------------


class _AdminReadRequest:
    def __init__(self) -> None:
        self.headers: Dict[str, str] = {}
        self.client = None
        self.scope: Dict[str, Any] = {}


def _patch_admin(monkeypatch):
    gate_calls: list = []

    def permission(request, scope):
        gate_calls.append(("permission", scope))
        return 7

    def host(request):
        gate_calls.append(("host", None))

    manager = MagicMock()
    manager.list_requests.return_value = {
        "requests": [],
        "total": 0,
        "page": 1,
        "per_page": 50,
        "pages": 0,
    }
    manager.get_status_summary.return_value = {}
    manager.get_traffic_data.return_value = []

    monkeypatch.setattr("src.api.routes.admin.plexijoin.check_host_restriction", host)
    monkeypatch.setattr(
        "src.api.routes.admin.plexijoin.require_admin_permission", permission
    )
    monkeypatch.setattr(
        "src.api.routes.admin.plexijoin._get_plexijoin_manager",
        lambda request, **kwargs: manager,
    )
    return gate_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    ["list_requests", "get_status", "get_traffic"],
)
async def test_admin_read_requires_plexijoin_manage_scope(monkeypatch, handler):
    from src.api.routes.admin import plexijoin as admin_plexijoin

    gate_calls = _patch_admin(monkeypatch)
    await getattr(admin_plexijoin, handler)(_AdminReadRequest())  # type: ignore[arg-type]

    assert ("permission", SCOPE) in gate_calls, (
        f"{handler} must require the {SCOPE} scope, not just authentication"
    )
