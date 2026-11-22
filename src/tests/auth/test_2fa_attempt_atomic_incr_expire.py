"""Regression: the Valkey-backed 2FA guessing limiter must set the bucket TTL
atomically with the first increment.

The limiter used to do two round trips::

    current = client.incr(full_key)
    if current == 1:
        client.expire(full_key, window_seconds)

That leaves a no-TTL window: a worker that dies (or whose EXPIRE call fails or
is delayed) between the two commands leaves the counter key with NO expiry.
Only the *first* increment installs the TTL, so every later attempt sees
``current > 1`` and never repairs it -- the account is locked out of 2FA
permanently. The shared cache rate limiter already fuses INCR and first-hit
EXPIRE into one Lua script for exactly this reason.

Run with: pytest -n 0 src/tests/auth/test_2fa_attempt_atomic_incr_expire.py
"""

import threading
import time

import pytest

from src.core.auth.exceptions import TwoFactorInvalidError
from src.core.auth.managers import twofactor as tf
from src.core.auth.managers.twofactor import TwoFactorMixin
from src.core.database.valkey_client import ValkeyOperationError


pytestmark = pytest.mark.unit

KEY = "ratelimit:auth_2fa_attempts:999"


class FakeValkey:
    """Fake store modelling the two-round-trip limiter and an atomic script.

    ``incr``/``expire`` reproduce the old non-atomic path (including a widened
    window in ``expire`` so concurrent callers can observe the missing TTL).
    ``eval_lua`` models the atomic INCR + first-hit EXPIRE script: the TTL is
    installed inside the same lock as the increment, before the caller sees the
    new count.
    """

    def __init__(self, *, fail_expire: bool = False):
        self.data = {}
        self.lock = threading.Lock()
        self.calls = []
        self.no_ttl_observations = []
        self.fail_expire = fail_expire

    def incr(self, key):
        with self.lock:
            self.calls.append("incr")
            entry = self.data.get(key)
            if entry is None:
                entry = {"count": 0, "ttl": None}
                self.data[key] = entry
            entry["count"] += 1
            if entry["ttl"] is None:
                self.no_ttl_observations.append(entry["count"])
            return entry["count"]

    def expire(self, key, ttl):
        # Simulate the network hop (and make the race deterministic).
        time.sleep(0.02)
        with self.lock:
            self.calls.append("expire")
            if self.fail_expire:
                raise ValkeyOperationError("simulated EXPIRE failure/crash")
            if key in self.data:
                self.data[key]["ttl"] = ttl

    def eval_lua(self, script, keys=None, args=None):
        with self.lock:
            self.calls.append("eval_lua")
            # The production script must really fuse both commands.
            assert "INCR" in script and "EXPIRE" in script
            entry = self.data.setdefault(keys[0], {"count": 0, "ttl": None})
            entry["count"] += 1
            if entry["count"] == 1:
                entry["ttl"] = int(args[0])
            return entry["count"]

    def ttl(self, key):
        with self.lock:
            return self.data.get(key, {}).get("ttl")


@pytest.fixture
def valkey(monkeypatch):
    client = FakeValkey()
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: client)
    monkeypatch.setattr(vc, "is_available", lambda: True)
    tf.clear_twofa_attempts()
    yield client
    tf.clear_twofa_attempts()


def _check(client, *, max_attempts=5, window=60):
    TwoFactorMixin._check_and_record_2fa_attempt(
        None, 999, max_attempts=max_attempts, window_seconds=window
    )


def test_first_attempt_sets_ttl_when_expire_roundtrip_is_lost(monkeypatch):
    """A failed/lost EXPIRE must not turn the counter into a permanent ban."""
    client = FakeValkey(fail_expire=True)
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: client)
    monkeypatch.setattr(vc, "is_available", lambda: True)
    tf.clear_twofa_attempts()

    # Pre-fix: the EXPIRE failure raises and leaves the key with no TTL.
    # Post-fix: the single atomic script installs the TTL before returning.
    _check(client)
    assert client.ttl(KEY) == 60


def test_concurrent_attempts_never_see_a_counter_without_ttl(valkey):
    """Under concurrency the TTL must exist at the instant count becomes 1."""
    threads = []
    allowed = []
    denied = []
    gate = threading.Barrier(16)

    def worker():
        gate.wait()
        try:
            _check(valkey, max_attempts=5, window=60)
        except TwoFactorInvalidError:
            denied.append(1)
        else:
            allowed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The budget itself is preserved: exactly max_attempts requests pass.
    assert len(allowed) == 5
    assert len(denied) == 11
    # No caller ever observed a counter whose first increment lacked a TTL.
    assert valkey.no_ttl_observations == []
    assert valkey.ttl(KEY) == 60
    # One atomic server-side command, never a bare INCR/EXPIRE pair.
    assert "incr" not in valkey.calls
    assert "expire" not in valkey.calls
