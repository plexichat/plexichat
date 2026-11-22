"""The signing-key guard must be per webhook, not process-global.

``verify_payload`` holds a mutex while it fetches the signing key, checks the
replay cache, verifies the Ed25519 signature and writes the replay marker. The
replay lookup and write are Valkey round trips, so a process-wide mutex made
every inbound delivery wait behind the slowest delivery for an *unrelated*
webhook. The guard is keyed by ``webhook_id``: the fetch/check/verify/consume
sequence stays atomic for one webhook (a rotation cannot swap its key
mid-verification, and one captured signature cannot be accepted twice), while
independent webhooks no longer block one another.
"""

import threading

import pytest

from src.core.database import cache as cache_module
from src.core.webhooks.manager.signature import _signature_lock_for


pytestmark = pytest.mark.unit


def _manager(setup):
    getter = getattr(setup["webhooks"], "_get_manager", None)
    return getter() if callable(getter) else setup["webhooks"]


def test_slow_valkey_for_one_webhook_does_not_block_another(
    webhook_with_token, monkeypatch
):
    """A slow replay-cache read for A must not hold up verification for B."""
    setup = webhook_with_token
    manager = _manager(setup)
    webhook_id = setup["webhook"].id
    other_id = int(webhook_id) + 100_000  # distinct webhook (no DB row needed)

    payload = {"event": "lock-scope"}
    headers = manager.sign_payload(webhook_id, payload)
    sig = headers["X-Plexichat-Signature"]
    ts = headers["X-Plexichat-Timestamp"]

    entered = threading.Event()
    release = threading.Event()
    first = {"seen": False}
    real_cache_get = cache_module.cache_get

    def slow_cache_get(key):
        if not first["seen"]:
            first["seen"] = True
            entered.set()
            # Simulate a slow Valkey round trip. Bounded so the test cannot hang.
            release.wait(timeout=5)
        return real_cache_get(key)

    monkeypatch.setattr(cache_module, "cache_get", slow_cache_get)

    a_result = {}

    def verify_a():
        a_result["ok"] = manager.verify_payload(webhook_id, payload, sig, ts)

    ta = threading.Thread(target=verify_a)
    ta.start()
    assert entered.wait(2), "webhook A never reached the replay-cache read"

    b_done = threading.Event()

    def verify_b():
        manager.verify_payload(other_id, payload, sig, ts)
        b_done.set()

    tb = threading.Thread(target=verify_b)
    tb.start()
    blocked = not b_done.wait(0.6)

    release.set()
    ta.join(timeout=5)
    tb.join(timeout=5)
    assert not ta.is_alive() and not tb.is_alive(), "worker threads did not finish"

    assert not blocked, (
        "verification for a different webhook was blocked by a slow "
        "replay-cache read for webhook A (process-global signing lock)"
    )


def test_same_webhook_shares_its_mutex_and_distinct_webhooks_do_not():
    """Non-vacuity: the guard is keyed, not removed or globally shared."""
    with _signature_lock_for(777) as first:
        with _signature_lock_for(777) as second:
            assert first is second, "one webhook must use one reentrant mutex"
    with _signature_lock_for(777) as a:
        with _signature_lock_for(778) as b:
            assert a is not b, "distinct webhooks must not share a mutex"
