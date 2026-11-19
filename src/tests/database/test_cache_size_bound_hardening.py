"""Regression: sizing a cached value must be bounded work, not O(container).

``_approx_cache_size`` runs on every cache write (and again whenever the size
bookkeeping is reconciled). Its old loop walked *every* element of a dict or
list before the byte cap could reject the value, so a single wide response
(~1M elements) cost hundreds of milliseconds per write and pinned the writer
while doing it. The byte cap was therefore useless against the very payloads
it was meant to stop: the cost was paid first, then the value was dropped.

These tests pin the new invariants:

* the number of ``getsizeof`` calls is bounded by the sample budget and does
  not grow with the container length;
* small containers keep the exact legacy estimate (no behaviour drift);
* oversized elements at either edge are still observed, so the byte cap still
  rejects a value whose size is dominated by one huge element;
* the caps, byte accounting and concurrency guarantees of the tier are intact.
"""

import sys
import threading

import pytest

from src.core.database.cache import base
from src.core.database.cache.base import (
    _mem_cache,
    mem_cache_delete,
    mem_cache_get,
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


class _CountingSys:
    """Drop-in for ``base.sys`` that only counts ``getsizeof`` visits."""

    def __init__(self):
        self.calls = 0

    def getsizeof(self, obj):
        self.calls += 1
        return sys.getsizeof(obj)


@pytest.mark.parametrize("kind", ["dict", "list"])
def test_sizing_work_is_bounded_by_sample_budget(monkeypatch, kind):
    counting = _CountingSys()
    monkeypatch.setattr(base, "sys", counting)

    small = {f"k{i}": i for i in range(100)} if kind == "dict" else list(range(100))
    large = (
        {f"k{i}": i for i in range(100_000)} if kind == "dict" else list(range(100_000))
    )

    assert base._approx_cache_size(small) > 0
    small_calls = counting.calls
    counting.calls = 0

    assert base._approx_cache_size(large) > 0
    large_calls = counting.calls

    # 100 elements are sized exactly; 100k elements visit at most the sample
    # budget. The work must not grow linearly with the container length.
    assert small_calls >= 100
    assert large_calls <= base._SIZE_SAMPLE_ITEMS + 4


def test_small_container_estimate_is_unchanged():
    """Containers inside the sample budget keep the exact legacy sum."""
    for value in (
        {f"k{i}": "v" for i in range(10)},
        [str(i) for i in range(10)],
        {f"k{i}": i for i in range(base._SIZE_SAMPLE_ITEMS)},
    ):
        expected = sys.getsizeof(value) + 64
        if isinstance(value, dict):
            for key, item in value.items():
                expected += (len(key) if isinstance(key, str) else 32) + 64
                expected += sys.getsizeof(item) + 32
        else:
            for item in value:
                expected += sys.getsizeof(item) + 32
        assert base._approx_cache_size(value) == expected


@pytest.mark.parametrize("where", ["head", "tail"])
def test_oversized_element_at_either_edge_still_respects_byte_cap(monkeypatch, where):
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_BYTES", 1024 * 1024)
    huge = "x" * (2 * 1024 * 1024)
    value = {f"k{i}": 1 for i in range(999)}
    if where == "head":
        value = {"huge": huge, **value}
    else:
        value["huge"] = huge

    assert base._approx_cache_size(value) > base._MEM_CACHE_MAX_BYTES
    mem_cache_set("oversized:edge", value, 60)
    assert "oversized:edge" not in _mem_cache
    assert base._mem_cache_total_bytes == 0
    assert sum(base._mem_cache_sizes.values()) == base._mem_cache_total_bytes


def test_wide_values_still_capped_and_accounted(monkeypatch):
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_BYTES", 16 * 1024)
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_SIZE", 16)
    for i in range(200):
        mem_cache_set(f"wide:{i}", list(range(200)), 60)

    assert len(_mem_cache) <= 16
    assert base._mem_cache_total_bytes <= 16 * 1024
    assert sum(base._mem_cache_sizes.values()) == base._mem_cache_total_bytes
    assert mem_cache_delete("wide:199") or "wide:199" not in _mem_cache
    assert sum(base._mem_cache_sizes.values()) == base._mem_cache_total_bytes


def test_concurrent_wide_writes_keep_caps_and_accounting(monkeypatch):
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_BYTES", 32 * 1024)
    monkeypatch.setattr(base, "_MEM_CACHE_MAX_SIZE", 24)
    errors = []

    def writer(offset):
        try:
            for i in range(150):
                key = f"stress:{offset}:{i}"
                mem_cache_set(key, {f"k{j}": j for j in range(300)}, 60)
                mem_cache_get(key)
                if i % 10 == 0:
                    mem_cache_delete(key)
        except Exception as exc:  # noqa: BLE001 - surface any race in the assertion
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15.0)

    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert len(_mem_cache) <= 24
    assert base._mem_cache_total_bytes <= 32 * 1024
    assert sum(base._mem_cache_sizes.values()) == base._mem_cache_total_bytes
