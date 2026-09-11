"""F7 regression: sticker-create fallback must not downgrade to per-process memory.

Pre-fix, ``_check_sticker_create_rate_limit`` did::

    if not ratelimit.is_setup():
        ratelimit.setup(storage_backend=MemoryStorage(), enable_global_limit=False)

which gives every worker its own quota (N x total) and disables the global
limiter. Post-fix the lazy path must use a shared backend with the global
limiter enabled, or fail closed with an honest 503 (with Retry-After)
instead of silently skipping the check.
"""

import pytest
from fastapi import HTTPException

import src.api.routes.stickers as stickers_route
from src.core import ratelimit
from src.core.ratelimit.storage import MemoryStorage
from src.core.ratelimit.storage.base import RateLimitStorage


class FakeSharedStorage(RateLimitStorage):
    """Minimal functional stand-in for a shared backend (Valkey/DB)."""

    def __init__(self):
        self._buckets = {}

    def get_bucket(self, key):
        return self._buckets.get(key)

    def set_bucket(self, key, state, ttl=None):
        self._buckets[key] = dict(state)

    def delete_bucket(self, key):
        return self._buckets.pop(key, None) is not None

    def get_keys_by_prefix(self, prefix):
        return [k for k in self._buckets if k.startswith(prefix)]

    def delete_by_prefix(self, prefix):
        doomed = [k for k in self._buckets if k.startswith(prefix)]
        for k in doomed:
            del self._buckets[k]
        return len(doomed)

    def clear_all(self):
        self._buckets.clear()

    def increment(self, key, field, amount=1):
        state = self._buckets.setdefault(key, {})
        state[field] = int(state.get(field, 0)) + amount
        return state[field]

    def get_and_set(self, key, field, value, default=None):
        state = self._buckets.setdefault(key, {})
        prev = state.get(field, default)
        state[field] = value
        return prev

    def add_to_list(self, key, field, value, max_size=1000):
        state = self._buckets.setdefault(key, {})
        lst = state.get(field, [])
        if not isinstance(lst, list):
            lst = []
        lst.append(value)
        state[field] = lst[-max_size:]
        return len(state[field])

    def trim_list(self, key, field, min_value):
        state = self._buckets.get(key, {})
        lst = state.get(field, [])
        if not isinstance(lst, list):
            return 0
        kept = [v for v in lst if v >= min_value]
        removed = len(lst) - len(kept)
        state[field] = kept
        return removed

    def acquire_lock(self, key, timeout=1.0):
        return "fake-token"

    def release_lock(self, key, token=None):
        return None

    def eval_token_bucket(self, key, capacity, refill_rate, cost, ttl=86400):
        return True, capacity, 0.0


@pytest.fixture
def _reset_ratelimit():
    """Save/restore the global ratelimit module state around each test."""
    prev_manager = ratelimit._manager
    prev_complete = ratelimit._setup_complete
    ratelimit._manager = None
    ratelimit._setup_complete = False
    try:
        yield
    finally:
        ratelimit._manager = prev_manager
        ratelimit._setup_complete = prev_complete


def test_lazy_setup_uses_shared_storage_with_global_limit(
    monkeypatch, _reset_ratelimit
):
    """Fallback setup must use shared storage + global limit enabled."""
    assert ratelimit.is_setup() is False

    calls = {}
    sentinel_storage = FakeSharedStorage()

    monkeypatch.setattr(
        stickers_route, "_build_shared_ratelimit_storage", lambda: sentinel_storage
    )

    real_setup = ratelimit.setup

    def _spy_setup(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return real_setup(*args, **kwargs)

    monkeypatch.setattr(ratelimit, "setup", _spy_setup)

    # Must not raise: shared backend is available in this test.
    stickers_route._check_sticker_create_rate_limit(user_id=12345)

    assert "kwargs" in calls, "expected lazy ratelimit.setup() call"
    assert calls["kwargs"].get("enable_global_limit") is True, (
        "fallback must keep the global limiter enabled"
    )
    storage = calls["kwargs"].get("storage_backend")
    assert storage is sentinel_storage
    assert not isinstance(storage, MemoryStorage), (
        "fallback must not downgrade to per-process MemoryStorage"
    )


def test_budget_shape_unchanged_20_per_minute(monkeypatch, _reset_ratelimit):
    """Budget stays 20/min: first 20 pass, 21st gets 429 (never 503)."""
    monkeypatch.setattr(
        stickers_route,
        "_build_shared_ratelimit_storage",
        lambda: FakeSharedStorage(),
    )
    uid = 777001
    for _ in range(20):
        stickers_route._check_sticker_create_rate_limit(user_id=uid)
    with pytest.raises(HTTPException) as excinfo:
        stickers_route._check_sticker_create_rate_limit(user_id=uid)
    assert excinfo.value.status_code == 429


def test_fail_closed_503_when_no_shared_backend(monkeypatch, _reset_ratelimit):
    """With no shared backend available, fail closed with 503 + Retry-After."""
    assert ratelimit.is_setup() is False

    monkeypatch.setattr(stickers_route, "_build_shared_ratelimit_storage", lambda: None)

    def _no_setup(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("setup must not be called without a shared backend")

    monkeypatch.setattr(ratelimit, "setup", _no_setup)

    with pytest.raises(HTTPException) as excinfo:
        stickers_route._check_sticker_create_rate_limit(user_id=999)

    assert excinfo.value.status_code == 503
    assert excinfo.value.headers is not None
    assert "Retry-After" in excinfo.value.headers


def test_no_memory_storage_fallback_in_source():
    """Static guard: the per-process downgrade must not reappear."""
    import inspect

    src = inspect.getsource(stickers_route._check_sticker_create_rate_limit)
    assert "MemoryStorage" not in src
    assert "enable_global_limit=False" not in src
