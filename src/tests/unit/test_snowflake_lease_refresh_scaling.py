"""Scaled-worker lease-refresh regressions.

The node-id lease used to refresh with a dedicated thread per lease, each
doing a tight GET-then-EXPIRE poll (two Valkey round trips per tick). These
tests pin the batched, throttled, single-round-trip behaviour while keeping the
uniqueness guarantees: atomic claim, token check before refresh, lost-lease
detection, and no re-arming of a stolen lease.
"""

import threading

import pytest

import src.core.database.valkey_node_id as vni


class _StoreClient:
    """Minimal Valkey stand-in with call counters (no ``eval_lua``)."""

    def __init__(self):
        self.store = {}
        self.get_calls = 0
        self.expire_calls = 0
        self.eval_calls = 0

    def set_nx(self, key, value, ttl=None):
        if key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        self.get_calls += 1
        return self.store.get(key)

    def expire(self, key, ttl):
        self.expire_calls += 1
        return key in self.store

    def delete(self, key):
        return self.store.pop(key, None) is not None


class NoEvalClient(_StoreClient):
    """A lightweight client: refresh must fall back to GET + EXPIRE."""


class LeaseClient(_StoreClient):
    """A Valkey client that also exposes the atomic ``eval_lua`` helper."""

    def eval_lua(self, script, keys=None, args=None):
        self.eval_calls += 1
        key, token = keys[0], args[0]
        return 1 if self.store.get(key) == token else 0


@pytest.fixture(autouse=True)
def _isolate_refresh_state():
    """Keep the process-wide refresher registry isolated between tests."""
    with vni._refresh_lock:
        saved = dict(vni._live_leases)
        vni._live_leases.clear()
    try:
        yield
    finally:
        with vni._refresh_lock:
            vni._live_leases.clear()
            vni._live_leases.update(saved)


def test_refresh_uses_one_atomic_round_trip():
    client = LeaseClient()
    lease = vni.claim_node_id(client, start_refresh=False)

    assert vni.refresh_lease(lease) == "ok"
    assert client.eval_calls == 1
    # No separate GET or EXPIRE: the Lua script did both atomically.
    assert client.get_calls == 0
    assert client.expire_calls == 0


def test_refresh_falls_back_to_get_expire_without_eval():
    client = NoEvalClient()
    lease = vni.claim_node_id(client, start_refresh=False)

    assert vni.refresh_lease(lease) == "ok"
    assert client.get_calls == 1
    assert client.expire_calls == 1
    assert client.eval_calls == 0


def test_lost_lease_is_detected_and_not_extended():
    client = LeaseClient()
    lease = vni.claim_node_id(client, start_refresh=False)
    client.store[lease.key] = "claimed-by-another-worker"

    assert vni.refresh_lease(lease) == "lost"
    assert client.eval_calls == 1


def test_lost_lease_fallback_does_not_expire():
    client = NoEvalClient()
    lease = vni.claim_node_id(client, start_refresh=False)
    client.store[lease.key] = "claimed-by-another-worker"

    assert vni.refresh_lease(lease) == "lost"
    assert client.expire_calls == 0


def test_transient_refresh_error_is_retriable():
    class FlakyClient(LeaseClient):
        def eval_lua(self, *a, **k):
            raise OSError("connection reset")

    client = FlakyClient()
    lease = vni.claim_node_id(client, start_refresh=False)
    assert vni.refresh_lease(lease) == "error"
    # A transient error must NOT deactivate the lease.
    assert lease._active is True


def test_single_loop_refreshes_all_due_leases_in_one_batch(monkeypatch):
    client = LeaseClient()
    leases = [vni.claim_node_id(client, start_refresh=False) for _ in range(8)]
    with vni._refresh_lock:
        for lease in leases:
            vni._live_leases[lease] = 0.0  # mark every lease as due now

    # Drive exactly one iteration of the shared loop without real sleeping.
    checks = {"n": 0}

    def is_set():
        checks["n"] += 1
        return checks["n"] > 1

    monkeypatch.setattr(vni._refresh_stop, "is_set", is_set)
    monkeypatch.setattr(vni._refresh_wake, "clear", lambda: None)
    monkeypatch.setattr(vni._refresh_wake, "wait", lambda *a, **k: True)

    vni._refresher_loop()

    assert client.eval_calls == 8  # one atomic call per due lease, one thread
    assert client.get_calls == 0
    assert all(lease._active for lease in leases)
    assert all(lease in vni._live_leases for lease in leases)


def test_batch_deactivates_a_stolen_lease_without_rearming(monkeypatch):
    client = LeaseClient()
    leases = [vni.claim_node_id(client, start_refresh=False) for _ in range(4)]
    stolen = leases[2]
    client.store[stolen.key] = "another-worker"
    with vni._refresh_lock:
        for lease in leases:
            vni._live_leases[lease] = 0.0

    checks = {"n": 0}

    def is_set():
        checks["n"] += 1
        return checks["n"] > 1

    monkeypatch.setattr(vni._refresh_stop, "is_set", is_set)
    monkeypatch.setattr(vni._refresh_wake, "clear", lambda: None)
    monkeypatch.setattr(vni._refresh_wake, "wait", lambda *a, **k: True)

    vni._refresher_loop()

    assert stolen._active is False
    assert stolen not in vni._live_leases
    # A lost lease can never be re-registered, so its node id is not reused.
    stolen.start()
    assert stolen not in vni._live_leases
    assert sum(lease._active for lease in leases) == 3


def test_refresher_thread_is_singleton():
    vni._ensure_refresher_running()
    first = vni._refresh_thread
    assert first is not None
    vni._ensure_refresher_running()
    assert vni._refresh_thread is first
    named = [t for t in threading.enumerate() if t.name == "snowflake-node-id-lease"]
    assert len(named) == 1


def test_refresh_schedule_is_throttled_and_jittered():
    client = LeaseClient()
    lease = vni.claim_node_id(client, ttl=300, start_refresh=False)
    assert vni._refresh_interval(300) == 100  # ttl // 3, never a tight poll

    import time

    before = time.monotonic()
    due_times = [vni._next_due(lease) for _ in range(50)]
    after = time.monotonic()
    # Always in the future, never later than one full interval (< ttl), and
    # spread across leases/replicas rather than a single lockstep instant.
    assert all(before + 80 <= due <= after + 100 for due in due_times)
    assert len(set(round(d, 4) for d in due_times)) > 1


def test_release_unregisters_and_deletes_only_owned_lease():
    client = LeaseClient()
    lease = vni.claim_node_id(client, start_refresh=False)
    with vni._refresh_lock:
        vni._live_leases[lease] = 0.0

    lease.release()

    assert lease not in vni._live_leases
    assert lease._active is False
    assert client.store.get(lease.key) is None
