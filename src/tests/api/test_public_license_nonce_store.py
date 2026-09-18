"""NonceStore falls back to memory when Valkey is disabled/unreachable.

Regression test: ``get_client()`` returns the process-wide ValkeyClient
instance even when Valkey is disabled (``setup()`` stores the disabled
instance in the module global), so a bare ``is not None`` check in
``NonceStore._get_valkey`` routed single-worker installs into the Valkey
path and every ``GET /public/license/challenge`` failed with 503.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes.public_license import NonceStore


def _disabled_client():
    class Disabled:
        enabled = False
        _connected = False
        _aconnected = False

        def set(self, *args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("Valkey must not be touched when disabled")

        def eval_lua(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("Valkey must not be touched when disabled")

    return Disabled()


def test_get_valkey_returns_none_when_client_disabled(monkeypatch) -> None:
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: _disabled_client())
    monkeypatch.setattr(vc, "is_available", lambda: False)
    store = NonceStore()
    assert store._get_valkey() is None


def test_issue_and_consume_use_memory_when_valkey_unavailable(monkeypatch) -> None:
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: _disabled_client())
    monkeypatch.setattr(vc, "is_available", lambda: False)
    store = NonceStore()
    nonce, ttl = store.issue()
    assert nonce and ttl > 0
    assert store.consume(nonce) is True
    # Single-use: replay is rejected.
    assert store.consume(nonce) is False


def test_get_valkey_returns_client_when_available(monkeypatch) -> None:
    import src.core.database.valkey_client as vc

    sentinel = object()
    monkeypatch.setattr(vc, "get_client", lambda: sentinel)
    monkeypatch.setattr(vc, "is_available", lambda: True)
    store = NonceStore()
    assert store._get_valkey() is sentinel


def _failing_client(exc: Exception = RuntimeError("boom")):
    class Failing:
        def set(self, *args, **kwargs):
            raise exc

        def eval_lua(self, *args, **kwargs):
            raise exc

    return Failing()


def test_issue_fails_closed_when_valkey_errors(monkeypatch) -> None:
    """A Valkey SET failure must surface as 503, never a memory nonce.

    Falling through to the in-process store would mint a split-brain nonce
    that a different worker could never consume (or, worse, that could be
    replayed across workers).
    """
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: _failing_client())
    monkeypatch.setattr(vc, "is_available", lambda: True)
    store = NonceStore()
    with pytest.raises(HTTPException) as exc_info:
        store.issue()
    assert exc_info.value.status_code == 503


def test_consume_fails_closed_when_valkey_errors(monkeypatch) -> None:
    """A Valkey EVAL failure must surface as 503, never as ``False``.

    Regression test: ``consume()`` used to swallow the error and return
    ``False``, so ``GET /public/license/export`` reported
    ``401 Invalid nonce`` for a server-side outage -- misleading the
    operator and forcing needless re-issues for a nonce that may never
    have been consumed.
    """
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: _failing_client())
    monkeypatch.setattr(vc, "is_available", lambda: True)
    store = NonceStore()
    with pytest.raises(HTTPException) as exc_info:
        store.consume("whatever")
    assert exc_info.value.status_code == 503


def test_consume_uses_valkey_result_when_available(monkeypatch) -> None:
    """EVAL 1/0 maps to consumed True / unknown-or-used False."""
    import src.core.database.valkey_client as vc

    results = [1, 0]

    class Scripted:
        def eval_lua(self, *args, **kwargs):
            return results.pop(0)

    monkeypatch.setattr(vc, "get_client", lambda: Scripted())
    monkeypatch.setattr(vc, "is_available", lambda: True)
    store = NonceStore()
    assert store.consume("nonce-a") is True
    assert store.consume("nonce-b") is False


def test_len_counts_valkey_backend(monkeypatch) -> None:
    """``len()`` must reflect the active backend, not the empty fallback."""
    import src.core.database.valkey_client as vc

    seen = {}

    class Counting:
        def keys(self, pattern):
            seen["pattern"] = pattern
            return ["lic_nonce:a", "lic_nonce:b", "lic_nonce:c"]

    monkeypatch.setattr(vc, "get_client", lambda: Counting())
    monkeypatch.setattr(vc, "is_available", lambda: True)
    store = NonceStore()
    assert len(store) == 3
    assert seen["pattern"] == "lic_nonce:*"


def test_len_falls_back_to_memory_on_valkey_error(monkeypatch) -> None:
    """``len()`` is a monitoring helper: it must never raise."""
    import src.core.database.valkey_client as vc

    class KeysFailing:
        def keys(self, pattern):
            raise RuntimeError("boom")

    monkeypatch.setattr(vc, "get_client", lambda: KeysFailing())
    monkeypatch.setattr(vc, "is_available", lambda: True)
    store = NonceStore()
    assert len(store) == 0


def test_len_counts_memory_when_valkey_unavailable(monkeypatch) -> None:
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: _disabled_client())
    monkeypatch.setattr(vc, "is_available", lambda: False)
    store = NonceStore()
    assert len(store) == 0
    nonce, _ = store.issue()
    assert nonce and len(store) == 1
    assert store.consume(nonce) is True
    assert len(store) == 0
