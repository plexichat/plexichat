"""Regression invariants for the public license challenge-response flow.

The challenge nonce is the single-use capability that gates the unauthenticated
``GET /public/license/export`` endpoint. These tests pin the properties that
make a captured (nonce, signature) pair non-replayable:

* atomic single-use consumption even under a true multi-thread race (no
  check-then-delete TOCTOU), and
* end-to-end rejection of a replayed, otherwise-valid signed challenge.

The Valkey backend achieves the same guarantee via a single Lua script; a
separate test asserts that consume() issues exactly one atomic EVAL rather than
a Python-level GET followed by DEL.
"""

from __future__ import annotations

import asyncio
import base64
import threading

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

import src.api.routes.public_license as pl
from src.api.routes.public_license import NonceStore


def _memory_store(monkeypatch) -> NonceStore:
    """A fresh in-process store with Valkey explicitly disabled."""
    store = NonceStore(ttl_seconds=300)
    monkeypatch.setattr(store, "_get_valkey", lambda: None)
    return store


def test_concurrent_consume_has_exactly_one_winner(monkeypatch) -> None:
    """A nonce raced by many callers must be consumed by exactly one of them.

    A check-then-delete implementation would let several threads all observe
    the nonce as present before any of them removed it, producing multiple
    successful license exports from a single challenge. ``consume()`` removes
    the entry under the store lock (and the Valkey path uses one atomic Lua
    EVAL), so there can be only one winner.
    """
    store = _memory_store(monkeypatch)
    nonce, _ = store.issue()

    workers = 16
    barrier = threading.Barrier(workers)
    outcomes: list[bool] = []

    def race() -> None:
        barrier.wait()
        outcomes.append(store.consume(nonce))

    threads = [threading.Thread(target=race) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(outcomes) == workers
    assert sum(outcomes) == 1, "exactly one concurrent consumer may win"


def test_signed_challenge_cannot_be_replayed_end_to_end(monkeypatch) -> None:
    """A valid (nonce, signature) pair grants exactly one license export.

    The signature proves key possession; the single-use nonce is what stops a
    caller who captures the pair (e.g. from the export URL in an access log)
    from exporting the license a second time.
    """
    seed = bytes(range(32))
    private = Ed25519PrivateKey.from_private_bytes(seed)
    pub_raw = private.public_key().public_bytes_raw()
    from src.utils.common_utils.utils.licensing.core import LicenseManager

    monkeypatch.setattr(
        LicenseManager,
        "_PUBLIC_KEY_BASE64",
        base64.b64encode(pub_raw).decode("ascii"),
        raising=False,
    )
    pl._public_key_cache.clear()
    monkeypatch.setattr(pl, "nonce_store", _memory_store(monkeypatch))

    import src.utils.licensing as licensing

    monkeypatch.setattr(licensing, "is_free_tier", lambda: False)
    monkeypatch.setattr(licensing, "to_dict", lambda: {"sku": "test"})
    monkeypatch.setattr(licensing, "get_validation_result", lambda: None)
    monkeypatch.setattr(licensing, "get_instance_id", lambda: "inst-1")
    monkeypatch.setattr(licensing, "get_expiry_timestamp", lambda: 0)

    nonce, _ = pl.nonce_store.issue()
    signature = base64.b64encode(private.sign(nonce.encode("utf-8"))).decode("ascii")

    first = asyncio.run(pl.export_license(nonce=nonce, signature=signature))
    assert first["license"] == {"sku": "test"}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(pl.export_license(nonce=nonce, signature=signature))
    assert exc.value.status_code == 401


def test_valkey_consume_is_a_single_atomic_eval(monkeypatch) -> None:
    """Valkey consumption is one GET+DEL Lua script, not a Python TOCTOU."""
    script = pl._CONSUME_LUA
    assert script.upper().count("GET") == 1
    assert script.upper().count("DEL") == 1
    assert "if v == false" in script

    calls: list[str] = []

    class AtomicClient:
        def eval_lua(self, lua, keys, args):
            calls.append("eval")
            # Emulate the Lua script: GET+DEL returns 1 only for the first call.
            return 1 if len(calls) == 1 else 0

    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: AtomicClient())
    monkeypatch.setattr(vc, "is_available", lambda: True)
    store = NonceStore()
    assert store.consume("nonce") is True
    assert store.consume("nonce") is False
    assert calls == ["eval", "eval"]
