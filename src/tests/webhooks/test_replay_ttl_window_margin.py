"""Regression: the Ed25519 inbound replay marker must outlive the signature.

A signature accepted at the EARLIEST valid instant (timestamp - window, e.g.
a signer whose clock leads the verifier by the full skew) stays acceptable
until timestamp + window, because the skew check uses ``>`` and skew == window
passes. The replay marker is written at first acceptance, so it must live for
MORE than the 2x-window gap between those two instants. With a TTL of exactly
2x the window, a replay landing on the last valid instant finds an expired
marker and is accepted a second time.
"""

import pytest

import src.core.database.cache as cache_module
import src.core.database.valkey_client as valkey_module
from src.core.webhooks.manager.signature import (
    WEBHOOK_REPLAY_WINDOW_SECONDS,
    _REPLAY_CACHE_TTL_SECONDS,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

_SIG = "X-Plexichat-Signature"
_TS = "X-Plexichat-Timestamp"


class _ClockCache:
    """TTL-aware in-memory cache driven by an explicit fake clock (seconds)."""

    def __init__(self):
        self.store = {}
        self.now = 0.0

    def get(self, key):
        entry = self.store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self.now >= expires_at:
            self.store.pop(key, None)
            return None
        return value

    def set(self, key, value, ttl=None):
        self.store[key] = (value, self.now + (ttl or 0))
        return True


def test_replay_marker_outlives_last_valid_instant(webhook_with_token, monkeypatch):
    setup = webhook_with_token
    manager = setup["webhooks"]
    wid = setup["webhook"].id
    payload = {"event": "ttl-window-boundary"}
    headers = manager.sign_payload(wid, payload)
    ts = int(headers[_TS])
    window_ms = WEBHOOK_REPLAY_WINDOW_SECONDS * 1000

    # Force the in-process replay-cache path with a TTL-aware fake cache.
    monkeypatch.setattr(valkey_module, "get_client", lambda: None)
    clock_cache = _ClockCache()
    monkeypatch.setattr(cache_module, "cache_get", clock_cache.get)
    monkeypatch.setattr(cache_module, "cache_set", clock_cache.set)

    # First acceptance at the earliest valid instant: signature - window.
    # skew == window is NOT rejected (the guard is ``>``), so this succeeds
    # and writes the replay marker at fake time 0 with the full TTL.
    assert (
        manager.verify_payload(
            wid, payload, headers[_SIG], headers[_TS], now_ms=ts - window_ms
        )
        is True
    )
    assert len(clock_cache.store) == 1

    # Advance the fake clock by exactly 2x the window: the time between the
    # first valid instant (signature - window) and the last valid instant
    # (signature + window). The marker is now exactly 2x-window seconds old.
    clock_cache.now = float(2 * WEBHOOK_REPLAY_WINDOW_SECONDS)

    # Replay at the last valid instant. With a TTL of exactly 2x the window
    # the marker has just expired, so the signature verifies a second time;
    # with the margin it is still alive and the replay is refused.
    assert (
        manager.verify_payload(
            wid, payload, headers[_SIG], headers[_TS], now_ms=ts + window_ms
        )
        is False
    )

    # Direct invariant: the marker lifetime must strictly exceed the total
    # exposure span, never merely equal it.
    assert _REPLAY_CACHE_TTL_SECONDS > 2 * WEBHOOK_REPLAY_WINDOW_SECONDS
