"""NonceStore falls back to memory when Valkey is disabled/unreachable.

Regression test: ``get_client()`` returns the process-wide ValkeyClient
instance even when Valkey is disabled (``setup()`` stores the disabled
instance in the module global), so a bare ``is not None`` check in
``NonceStore._get_valkey`` routed single-worker installs into the Valkey
path and every ``GET /public/license/challenge`` failed with 503.
"""

from __future__ import annotations

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
