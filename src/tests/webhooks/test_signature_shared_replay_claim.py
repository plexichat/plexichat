"""Cross-process replay protection for webhook signatures.

The in-process lock only serializes threads inside one worker. Two API
workers behind a load balancer can both see a cache miss, both verify the
same captured signature and both accept it: ``cache_get`` + ``cache_set`` is
not atomic. The consume step must therefore use the shared cache tier's
conditional ``SET NX`` so exactly one process wins the claim.
"""

import base64

import pytest

import src.core.database.cache as cache_module
import src.core.database.valkey_client as valkey_module
from src.core.webhooks.manager.signature import _replay_cache_key

pytestmark = [pytest.mark.unit, pytest.mark.security]

_SIG = "X-Plexichat-Signature"
_TS = "X-Plexichat-Timestamp"


class _SharedClient:
    """Minimal Valkey stand-in with an atomic ``set_nx``."""

    def __init__(self):
        self.store = {}
        self.set_calls = 0

    def set_nx(self, key, value, ttl=None):
        self.set_calls += 1
        if key in self.store:
            return False
        self.store[key] = value
        return True


def _use_shared_client(monkeypatch, client):
    monkeypatch.setattr(valkey_module, "get_client", lambda: client)
    monkeypatch.setattr(valkey_module, "is_available", lambda: True)


class TestSharedReplayClaim:
    def test_second_worker_cannot_consume_the_same_signature(
        self, webhook_with_token, monkeypatch
    ):
        setup = webhook_with_token
        manager = setup["webhooks"]
        wid = setup["webhook"].id
        payload = {"event": "shared-replay-claim"}
        headers = manager.sign_payload(wid, payload)

        shared = _SharedClient()
        _use_shared_client(monkeypatch, shared)

        # No early cache hit, and the legacy write must never be reached on
        # the shared path -- otherwise the race is still there.
        monkeypatch.setattr(cache_module, "cache_get", lambda key: None)

        def fail_set(*args, **kwargs):
            raise AssertionError("cache_set must not be used when SET NX is available")

        monkeypatch.setattr(cache_module, "cache_set", fail_set)

        first = manager.verify_payload(wid, payload, headers[_SIG], headers[_TS])
        second = manager.verify_payload(wid, payload, headers[_SIG], headers[_TS])

        assert first is True
        assert second is False
        assert shared.set_calls == 2
        assert len(shared.store) == 1
        replay_key = _replay_cache_key(wid, base64.b64decode(headers[_SIG]))
        assert replay_key in shared.store

    def test_claim_loss_rejects_without_another_verification(
        self, webhook_with_token, monkeypatch
    ):
        """A worker that loses SET NX is rejected even though its Ed25519
        check would have passed -- that is the cross-process replay window.
        """
        setup = webhook_with_token
        manager = setup["webhooks"]
        wid = setup["webhook"].id
        payload = {"event": "claim-loss"}
        headers = manager.sign_payload(wid, payload)

        class PreclaimedClient(_SharedClient):
            def set_nx(self, key, value, ttl=None):
                self.set_calls += 1
                # Simulate another process having won the claim just now.
                return False

        shared = PreclaimedClient()
        _use_shared_client(monkeypatch, shared)
        monkeypatch.setattr(cache_module, "cache_get", lambda key: None)
        monkeypatch.setattr(
            cache_module,
            "cache_set",
            lambda *a, **k: pytest.fail("cache_set must not be used on shared path"),
        )

        assert manager.verify_payload(wid, payload, headers[_SIG], headers[_TS]) is False
        assert shared.set_calls == 1

    def test_missing_shared_backend_falls_back_to_local_cache(
        self, webhook_with_token, monkeypatch
    ):
        """No Valkey: the existing in-process guarded cache still applies."""
        setup = webhook_with_token
        manager = setup["webhooks"]
        wid = setup["webhook"].id
        payload = {"event": "local-fallback"}
        headers = manager.sign_payload(wid, payload)

        monkeypatch.setattr(valkey_module, "get_client", lambda: None)
        store = {}
        monkeypatch.setattr(cache_module, "cache_get", lambda key: store.get(key))
        monkeypatch.setattr(
            cache_module, "cache_set", lambda key, value, ttl=None: store.__setitem__(key, value)
        )

        assert manager.verify_payload(wid, payload, headers[_SIG], headers[_TS]) is True
        assert manager.verify_payload(wid, payload, headers[_SIG], headers[_TS]) is False
