"""Regression tests for the two admin PlexiJoin connection GET endpoints.

``GET /api/v1/admin/plexijoin/connections`` and ``.../connections/{id}`` were
slow because the manager decrypted one ``shared_key`` per listed row even
though the admin serializer never emits that field (O(page size) AEAD calls),
and because the COUNT wrapped a fully sorted query. They were also missing
the ``admin.plexijoin.manage`` scope check that every other admin route
enforces, letting an authenticated-but-unscoped admin read arbitrary
connection records by id.

These tests pin three things: identical output contract, bounded/constant
work on the read path, and the scope (IDOR) gate.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from src.api.routes.admin import plexijoin as route
from src.core.plexijoin import PlexiJoinManager

pytestmark = pytest.mark.integration

N_CONNECTIONS = 25
FIRST_ID = 1000
SCOPE = "admin.plexijoin.manage"


class _SpyEncryption:
    """Counts decrypt calls and can simulate a costly AEAD operation."""

    def __init__(self, delay: float = 0.0):
        self.decrypt_calls = 0
        self.delay = delay

    def encrypt(self, value: str) -> str:
        return "enc:" + value

    def decrypt(self, value: str) -> str:
        self.decrypt_calls += 1
        if self.delay:
            time.sleep(self.delay)
        return value[len("enc:") :]


@pytest.fixture
def seeded(db):
    """Insert a bounded set of connections with encrypted shared keys."""
    now = int(time.time() * 1000)
    for i in range(N_CONNECTIONS):
        db.execute(
            "INSERT OR IGNORE INTO plexijoin_connections "
            "(id, remote_instance_id, remote_url, shared_key_encrypted, status, "
            " connected_at, messages_in, messages_out, last_activity, note, "
            " created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                FIRST_ID + i,
                f"remote-{i}",
                f"https://remote{i}.example.com",
                f"enc:secret-{i}",
                "active" if i % 2 else "pending",
                now,
                i,
                i * 2,
                now,
                "a note",
                now + i,
                987654321098765432,  # admin snowflake -> decimal string
            ),
        )
    return db


@contextmanager
def _route_env(db, encryption, *, on_permission=None, patch_encryption=True):
    """Patch the route's auth/licence/DB deps; leave the endpoint logic real."""
    req = MagicMock(spec=Request)

    def _permission(request, permission):
        if on_permission is not None:
            return on_permission(request, permission)
        return 1

    patches = [
        patch.object(route, "check_host_restriction"),
        patch.object(route, "require_admin_permission", side_effect=_permission),
        patch("src.api.get_db", return_value=db),
        patch("utils.licensing.has_feature", return_value=True),
    ]
    if patch_encryption:
        patches.append(
            patch.object(route, "_encryption_service", return_value=encryption)
        )
    for p in patches:
        p.start()
    try:
        yield req
    finally:
        for p in reversed(patches):
            p.stop()


def _call_list(req):
    return asyncio.run(route.list_connections(req, status=None, page=1, per_page=50))


def _call_get(req, connection_id):
    return asyncio.run(route.get_connection(req, connection_id))


def _expected_connection(row):
    """Mirror ``_serialize_connection`` from the raw DB row."""
    return {
        "id": row["id"],
        "remote_instance_id": row["remote_instance_id"],
        "remote_url": row["remote_url"],
        "status": row["status"],
        "connected_at": row["connected_at"],
        "messages_in": row["messages_in"],
        "messages_out": row["messages_out"],
        "last_activity": row["last_activity"],
        "note": row["note"],
        "created_at": row["created_at"],
        "created_by": str(row["created_by"]),
    }


class TestAdminConnectionReadHardening:
    def test_output_identical_and_no_shared_key_leak(self, seeded):
        """Optimized admin reads return exactly the same JSON shape."""
        enc = _SpyEncryption()
        with _route_env(seeded, enc) as req:
            listed = _call_list(req)
            detail = _call_get(req, FIRST_ID + 3)

        one = seeded.fetch_one(
            "SELECT * FROM plexijoin_connections WHERE id = ?", (FIRST_ID + 3,)
        )
        assert listed["total"] == N_CONNECTIONS
        assert listed["page"] == 1
        assert listed["per_page"] == 50
        assert listed["pages"] == 1
        assert detail == _expected_connection(one)
        # Most-recent first: our seed gives created_at = now + i.
        assert listed["connections"][0]["id"] == FIRST_ID + N_CONNECTIONS - 1
        for payload in (*listed["connections"], detail):
            assert "shared_key" not in payload
            assert "shared_key_encrypted" not in payload

    def test_read_path_does_no_crypto_and_no_encryption_service(self, seeded):
        """List detail never decrypts a key nor imports/initialises crypto."""
        enc = _SpyEncryption()
        with patch.object(route, "_encryption_service") as svc:
            svc.return_value = enc
            with _route_env(seeded, enc, patch_encryption=False) as req:
                _call_list(req)
                _call_get(req, FIRST_ID)
        assert enc.decrypt_calls == 0
        # The read endpoint must not even ask for the encryption adapter.
        assert svc.call_count == 0

    def test_read_path_query_count_is_bounded(self, seeded):
        """COUNT + one page query for list; one lookup for detail. No O(n)."""
        enc = _SpyEncryption()
        calls = {"fetch_one": 0, "fetch_all": 0}
        orig_one, orig_all = seeded.fetch_one, seeded.fetch_all

        def _one(*a, **k):
            calls["fetch_one"] += 1
            return orig_one(*a, **k)

        def _all(*a, **k):
            calls["fetch_all"] += 1
            return orig_all(*a, **k)

        seeded.fetch_one, seeded.fetch_all = _one, _all
        try:
            with _route_env(seeded, enc) as req:
                _call_list(req)
                assert calls == {"fetch_one": 1, "fetch_all": 1}
                _call_get(req, FIRST_ID)
                assert calls == {"fetch_one": 2, "fetch_all": 1}
        finally:
            seeded.fetch_one, seeded.fetch_all = orig_one, orig_all

    def test_scope_gate_blocks_unscoped_admin_on_both_reads(self, seeded):
        """Missing scope used to allow any authenticated admin to read rows."""
        enc = _SpyEncryption()

        def _deny(request, permission):
            assert permission == SCOPE
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Insufficient permissions"}},
            )

        with _route_env(seeded, enc, on_permission=_deny) as req:
            for call in (lambda: _call_list(req), lambda: _call_get(req, FIRST_ID)):
                with pytest.raises(HTTPException) as exc:
                    call()
                assert exc.value.status_code == 403
        # No connection data touched when the scope gate rejects.
        assert enc.decrypt_calls == 0

    def test_scope_gate_is_invoked_with_federation_scope(self, seeded):
        seen = []

        def _record(request, permission):
            seen.append(permission)
            return 1

        enc = _SpyEncryption()
        with _route_env(seeded, enc, on_permission=_record) as req:
            _call_list(req)
            _call_get(req, FIRST_ID)
        assert seen == [SCOPE, SCOPE]

    def test_read_path_latency_under_budget(self, seeded):
        """Even with a slow crypto stub, reads stay far below the 20ms budget."""
        enc = _SpyEncryption(delay=0.0015)
        with _route_env(seeded, enc) as req:
            _call_list(req)  # warm
            _call_get(req, FIRST_ID)
            samples = []
            for _ in range(20):
                t0 = time.perf_counter()
                _call_list(req)
                samples.append((time.perf_counter() - t0) * 1000)
            samples.sort()
            median = samples[len(samples) // 2]
        assert median < 20, f"list median {median:.2f}ms exceeds 20ms budget"
        assert enc.decrypt_calls == 0

    def test_federation_callers_still_get_plaintext_key(self, seeded):
        """Default manager behavior (used by outbound federation) is intact."""
        enc = _SpyEncryption()
        mgr = PlexiJoinManager(seeded, encryption_service=cast(object, enc))
        detail = mgr.get_connection(FIRST_ID)
        assert detail["shared_key"] == "secret-0"
        assert enc.decrypt_calls == 1
