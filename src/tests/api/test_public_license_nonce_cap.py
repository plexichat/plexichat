"""Regression test: the unauthenticated license challenge store is bounded.

The public ``/public/license/challenge`` endpoint mints a nonce on every call.
Without a cap a single client can grow the in-process dict (or the shared
Valkey keyspace) without limit, exhausting memory. ``NonceStore.issue()`` now
fails closed with 503 once the outstanding-challenge cap is reached.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes import public_license as pl
from src.api.routes.public_license import NonceStore


def test_nonce_store_cap_rejects_issue_when_full(monkeypatch):
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: None)
    monkeypatch.setattr(vc, "is_available", lambda: False)

    store = NonceStore(ttl_seconds=300)
    # Fill to the cap without consuming or purging (short TTL window).
    for _ in range(pl._MAX_OUTSTANDING_NONCES):
        store.issue()
    assert len(store) == pl._MAX_OUTSTANDING_NONCES

    with pytest.raises(HTTPException) as exc:
        store.issue()
    assert exc.value.status_code == 503


def test_nonce_store_cap_checks_valkey_count(monkeypatch):
    """The cap must use the authoritative Valkey count when it is active."""
    import src.core.database.valkey_client as vc

    class Full:
        def keys(self, pattern):
            return ["lic_nonce:x"] * (pl._MAX_OUTSTANDING_NONCES + 1)

        def set(self, *args, **kwargs):
            raise AssertionError("SET must not be called when store is full")

    monkeypatch.setattr(vc, "get_client", lambda: Full())
    monkeypatch.setattr(vc, "is_available", lambda: True)

    store = NonceStore()
    with pytest.raises(HTTPException) as exc:
        store.issue()
    assert exc.value.status_code == 503
