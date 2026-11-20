"""Regression: size bookkeeping must not rescan the whole cache on every write.

``_reconcile_sizes_locked`` used to compare two ~1000-key ``dict_keys`` views on
every ``mem_cache_set``. That comparison is O(n) and allocates a temporary set
(~30us at the entry cap), and it runs under ``_mem_cache_lock`` -- so a burst of
cache writes serialised behind a scan of the entire key set.

The fast path now uses an O(1) length check (which catches the clear/insert
mutations callers and tests actually perform) plus a bounded periodic full
rebuild as a safety net for same-length swaps. These tests pin the bounded work
and the continued correctness of the byte accounting.
"""

import pytest

from src.core.database.cache import base
from src.core.database.cache.base import (
    _mem_cache,
    mem_cache_set,
)


@pytest.fixture(autouse=True)
def _isolate_mem_tier():
    _mem_cache.clear()
    base._mem_cache_sizes.clear()
    base._mem_cache_total_bytes = 0
    yield
    _mem_cache.clear()
    base._mem_cache_sizes.clear()
    base._mem_cache_total_bytes = 0


class _CountingKeys:
    """Set-like view that records key-set comparisons made against it."""

    def __init__(self, owner, keys):
        self._owner = owner
        self._keys = keys

    def __eq__(self, other):
        self._owner.comparisons += 1
        return self._keys == other

    def __getattr__(self, name):
        return getattr(self._keys, name)

    __hash__ = None


class _CountingSizes(dict):
    """Drop-in ``_mem_cache_sizes`` that counts O(n) key-set scans."""

    def __init__(self, source):
        super().__init__(source)
        self.comparisons = 0

    def keys(self):
        return _CountingKeys(self, super().keys())


@pytest.mark.unit
class TestReconcileFastPath:
    def test_full_key_set_is_not_compared_on_every_write(self, monkeypatch):
        # Fill the tier close to its 1000-entry cap so the old key-set
        # comparison would scan ~600 keys on every one of the writes below.
        for i in range(600):
            mem_cache_set(f"seed:{i}", i, 60)
        assert len(_mem_cache) == 600

        counting = _CountingSizes(base._mem_cache_sizes)
        monkeypatch.setattr(base, "_mem_cache_sizes", counting)

        for i in range(256):
            mem_cache_set(f"hot:{i}", i, 60)

        # Old behaviour: one full key-set comparison per write (256). New
        # behaviour: none on the O(1) length fast path; only the bounded
        # periodic rebuilds may compare, independently of the write count.
        assert counting.comparisons <= 3
        assert len(_mem_cache) == 856
        assert len(base._mem_cache_sizes) == len(_mem_cache)
        expected = sum(base._approx_cache_size(v[1]) for v in _mem_cache.values())
        assert base._mem_cache_total_bytes == expected

    def test_external_clear_is_detected_immediately(self):
        mem_cache_set("keep:1", "a", 60)
        mem_cache_set("keep:2", "b", 60)
        assert len(base._mem_cache_sizes) == 2

        # Tests and a few callers clear the module dict directly.
        _mem_cache.clear()

        mem_cache_set("fresh", "c", 60)
        assert list(base._mem_cache_sizes) == ["fresh"]
        assert base._mem_cache_total_bytes == base._approx_cache_size("c")

    def test_same_length_key_swap_is_reconciled(self):
        mem_cache_set("original", "a", 60)

        # A same-length external key swap is invisible to the O(1) length
        # check; the periodic full rebuild is the safety net for it.
        _mem_cache.pop("original")
        _mem_cache["replacement"] = (9999999999, "much bigger value")

        for i in range(256):
            mem_cache_set(f"probe:{i}", i, 60)

        expected = sum(base._approx_cache_size(v[1]) for v in _mem_cache.values())
        assert base._mem_cache_total_bytes == expected
        assert "replacement" in base._mem_cache_sizes
        assert "original" not in base._mem_cache_sizes
