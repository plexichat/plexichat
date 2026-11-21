"""Performance regression tests for config access and cache behavior.

These pin the *work* done on hot paths rather than wall-clock numbers that
would be flaky in CI. Where a timing bound is used, the cost is made explicit
with a deliberate sleep so the margin is unambiguous. Together they prevent a
regression back to re-reading config per request, unbounded caches, or
re-parsing unchanged log files.
"""

import os
import time

import pytest

from src.api import config as api_config
from src.api.lru_cache import LRUCache
from src.core.admin import logs as admin_logs
from src.core.config.snapshot import VersionedConfigSnapshot


@pytest.fixture(autouse=True)
def _reset_api_snapshot():
    api_config._api_config_snapshot.invalidate()
    admin_logs.clear_log_filter_index()
    yield
    api_config._api_config_snapshot.invalidate()
    admin_logs.clear_log_filter_index()


# --------------------------------------------------------------------------- #
# Config access                                                               #
# --------------------------------------------------------------------------- #


def test_snapshot_hot_path_loads_once_for_many_reads(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("api:\n  title: Perf\n")
    calls = []

    def loader():
        calls.append(time.perf_counter())
        return {"api": {"title": "Perf"}}

    snapshot = VersionedConfigSnapshot(loader, paths=[config_path])

    for _ in range(500):
        assert snapshot.get("api")["title"] == "Perf"

    assert len(calls) == 1, "hot config access must not re-read the source"


def test_snapshot_edit_triggers_exactly_one_rebuild(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("api:\n  title: Perf\n")
    calls = []
    snapshot = VersionedConfigSnapshot(
        lambda: calls.append(1) or {"v": len(calls)}, paths=[config_path]
    )

    for _ in range(100):
        snapshot.snapshot()
    assert len(calls) == 1

    future = time.time() + 5
    os.utime(config_path, (future, future))
    for _ in range(100):
        snapshot.snapshot()
    assert len(calls) == 2, "one edit must cause one rebuild, not one per read"


def test_cached_config_access_skips_deliberate_loader_cost(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("a: 1\n")
    loader_cost = 0.005
    calls = []

    def slow_loader():
        calls.append(1)
        time.sleep(loader_cost)
        return {"value": len(calls)}

    snapshot = VersionedConfigSnapshot(slow_loader, paths=[config_path])
    reads = 40

    start = time.perf_counter()
    for _ in range(reads):
        snapshot.snapshot()
    elapsed = time.perf_counter() - start

    # A regression to reloading per access would cost ~0.2s here.
    assert elapsed < reads * loader_cost / 2
    assert len(calls) == 1


def test_api_config_wiring_reuses_snapshot_value(monkeypatch):
    snapshot = api_config._api_config_snapshot
    sentinel = api_config.APIConfig(title="Perf sentinel")
    loads = []
    versions = []

    monkeypatch.setattr(snapshot, "_loader", lambda: loads.append(1) or sentinel)
    monkeypatch.setattr(
        snapshot, "_version_fn", lambda: versions.append(1) or ("version",)
    )
    snapshot.invalidate()

    results = [api_config.get_api_config() for _ in range(200)]

    # The snapshot value is materialised once; callers receive detached copies
    # (APIConfig is mutable config gating CORS/auth), so results are equal but
    # never the shared object itself.
    assert all(result == sentinel for result in results)
    assert all(result is not sentinel for result in results)
    assert len({id(result) for result in results}) == len(results)
    assert len(loads) == 1, "API config must be materialised once per version"
    # The short staleness ceiling throttles the (stat-backed) version check on
    # this per-request path; repeated reads inside the window hit memory only.
    assert len(versions) == 1, "version checks are throttled by the staleness ceiling"


# --------------------------------------------------------------------------- #
# Cache behavior                                                              #
# --------------------------------------------------------------------------- #


def test_lru_cache_hot_keys_never_miss_or_evict():
    cache = LRUCache(maxsize=64)
    for i in range(64):
        cache[i] = i

    for _ in range(1000):
        assert cache.get(7) == 7

    stats = cache.stats()
    assert stats["hits"] == 1000
    assert stats["misses"] == 0
    assert stats["evictions"] == 0
    assert stats["size"] == 64


def test_lru_cache_churn_stays_bounded():
    cache = LRUCache(maxsize=32)
    inserts = 5000
    for i in range(inserts):
        cache[i] = i

    assert len(cache) == 32
    assert cache.stats()["evictions"] == inserts - 32


def test_log_index_avoids_reparsing_unchanged_file(tmp_path, monkeypatch):
    (tmp_path / "app.log").write_text(
        "2026-11-16 12:00:00,000 - INFO - perf line\n" * 2000
    )
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)
    parses = []
    original = admin_logs._read_filtered

    def counting(*args, **kwargs):
        parses.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(admin_logs, "_read_filtered", counting)

    for offset in range(100):
        admin_logs.read_log_lines("app.log", limit=20, offset=offset)

    assert len(parses) == 1, "unchanged log + filter must be parsed once"
    assert admin_logs.index_stats()["entries"] == 1
