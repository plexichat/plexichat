"""Regression tests for contended Valkey lock acquisition under N workers.

``LockMixin`` used to poll a contended lock on a fixed 50 ms interval with no
jitter. Every worker that started contending at the same instant stayed phase
locked, so once the holder released, all N workers re-issued SET NX on the same
tick and N-1 of them burned a round trip only to resynchronise for the next
tick. The sync path also used ``time.time()`` (non-monotonic) for its deadline.

These tests pin the two fixes:

* full-jitter exponential backoff with a hard ceiling, and
* a monotonic deadline that cannot be shifted by a wall-clock adjustment.

They also simulate several workers contending over one shared keyspace to guard
mutual exclusion while the schedule is decorrelated.
"""

import asyncio
import importlib
import threading
import time

import pytest

from src.core.database.valkey_client import lock as lock_module
from src.core.database.valkey_client.lock import (
    _INITIAL_BACKOFF_S,
    _MAX_BACKOFF_S,
    LockMixin,
    _retry_delay,
)


# ---------------------------------------------------------------------------
# Shared, atomic fake keyspace + GLIDE-shaped handles
# ---------------------------------------------------------------------------


class _SharedStore:
    """One Valkey keyspace shared by every simulated worker handle."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiry: dict[str, float] = {}
        self.set_calls = 0
        self._lock = threading.RLock()

    def _live(self, key: str, now: float) -> bool:
        return key in self.data and self.expiry.get(key, now) > now

    def set_nx(self, key: str, value: str, ttl_ms: float) -> bool:
        with self._lock:
            self.set_calls += 1
            now = time.monotonic()
            if self._live(key, now):
                return False
            self.data[key] = value
            self.expiry[key] = now + (ttl_ms / 1000.0)
            return True

    def get(self, key: str):
        with self._lock:
            now = time.monotonic()
            if key in self.data and self.expiry.get(key, now) <= now:
                self.data.pop(key, None)
                self.expiry.pop(key, None)
                return None
            return self.data.get(key)

    def release_if_owned(self, key: str, token: str) -> int:
        with self._lock:
            if self.get(key) == token:
                self.data.pop(key, None)
                self.expiry.pop(key, None)
                return 1
            return 0


class _FakeSyncClient:
    def __init__(self, store: _SharedStore) -> None:
        self.store = store

    def set(self, key, value, conditional_set=None, expiry=None):
        glide_sync = importlib.import_module("glide_sync")
        ttl_ms = 30000.0
        if isinstance(expiry, glide_sync.ExpirySet):
            ttl_ms = float(expiry.value)
            if expiry.expiry_type != glide_sync.ExpiryType.MILLSEC:
                ttl_ms *= 1000.0
        acquired = self.store.set_nx(str(key), str(value), ttl_ms)
        return b"OK" if acquired else None

    def invoke_script(self, script, keys=None, args=None):
        token = args[0]
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return self.store.release_if_owned(keys[0], token)


class _FakeAsyncClient(_FakeSyncClient):
    async def set(self, *args, **kwargs):
        return super().set(*args, **kwargs)

    async def invoke_script(self, *args, **kwargs):
        return super().invoke_script(*args, **kwargs)


class _Harness(LockMixin):
    """A LockMixin with injected state, no config or real GLIDE connection."""

    def __init__(self, client=None, aclient=None, prefix="test:") -> None:
        self.enabled = True
        self._client = client
        self._connected = client is not None
        self._aclient = aclient
        self._aconnected = aclient is not None
        self._pubsub_handle = None
        self.key_prefix = prefix
        self.worker_id = "worker"


class _RecordingRNG:
    """Deterministic jitter source that records the bounds it was given."""

    def __init__(self, fraction: float = 0.5) -> None:
        self.fraction = fraction
        self.bounds: list[tuple[float, float]] = []

    def uniform(self, low: float, high: float) -> float:
        self.bounds.append((low, high))
        return low + self.fraction * (high - low)


# ---------------------------------------------------------------------------
# Backoff schedule (the pure property the fixed-interval code violated)
# ---------------------------------------------------------------------------


def test_retry_delay_is_exponential_jittered_and_bounded():
    rng = _RecordingRNG()
    delays = [_retry_delay(i, rng) for i in range(8)]

    ceilings = [high for _, high in rng.bounds]
    assert ceilings == [
        _INITIAL_BACKOFF_S,
        _INITIAL_BACKOFF_S * 2,
        _INITIAL_BACKOFF_S * 4,
        _INITIAL_BACKOFF_S * 8,
        _MAX_BACKOFF_S,
        _MAX_BACKOFF_S,
        _MAX_BACKOFF_S,
        _MAX_BACKOFF_S,
    ]
    # Each draw consulted the jitter source over [0, ceiling], and every
    # resulting delay stays within the hard ceiling.
    assert all(low == 0.0 for low, _ in rng.bounds)
    assert delays == [ceiling * 0.5 for ceiling in ceilings]
    assert all(0.0 <= delay <= _MAX_BACKOFF_S for delay in delays)


def test_retry_delay_uses_the_jitter_source_not_a_constant():
    rng = _RecordingRNG(fraction=0.25)
    _retry_delay(6, rng)
    assert len(rng.bounds) == 1
    assert rng.bounds[0] == (0.0, _MAX_BACKOFF_S)


# ---------------------------------------------------------------------------
# Multiple workers contending over one shared keyspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_workers_serialize_and_back_off(monkeypatch):
    store = _SharedStore()
    workers = [_Harness(aclient=_FakeAsyncClient(store)) for _ in range(8)]

    real_retry_delay = lock_module._retry_delay
    delays_seen: list[float] = []

    def _recording_delay(attempt, rng=None):
        delay = real_retry_delay(attempt, rng)
        delays_seen.append(delay)
        return delay

    monkeypatch.setattr(lock_module, "_retry_delay", _recording_delay)

    state = {"active": 0, "max_active": 0}
    acquired_tokens: list[str] = []

    async def run(index: int) -> None:
        token = await workers[index].aacquire_lock("hot", timeout=1.0)
        if token is None:
            return
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0)  # hold across a couple of loop turns
        await asyncio.sleep(0)
        state["active"] -= 1
        assert await workers[index].arelease_lock("hot", token) is True
        acquired_tokens.append(token)

    await asyncio.gather(*(run(i) for i in range(8)))

    # Mutual exclusion held despite contention, and every worker made progress.
    assert state["max_active"] == 1
    assert len(acquired_tokens) == 8
    assert len(set(acquired_tokens)) == 8
    # Contended workers actually took the backed-off path, within its ceiling.
    assert delays_seen, "expected contended retries to use backoff"
    assert all(0.0 <= d <= _MAX_BACKOFF_S for d in delays_seen)


def test_sync_workers_serialize_with_backoff():
    store = _SharedStore()
    workers = [_Harness(client=_FakeSyncClient(store)) for _ in range(6)]

    state = {"active": 0, "max_active": 0}
    guard = threading.Lock()
    acquired_tokens: list[str] = []

    def run(index: int) -> None:
        token = workers[index].acquire_lock("hot", timeout=2.0)
        if token is None:
            return
        with guard:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.005)
        with guard:
            state["active"] -= 1
        assert workers[index].release_lock("hot", token) is True
        acquired_tokens.append(token)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state["max_active"] == 1
    assert len(acquired_tokens) == 6
    assert len(set(acquired_tokens)) == 6
