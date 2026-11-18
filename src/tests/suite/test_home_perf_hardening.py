"""Regression tests for the PlexiSuite home/search feed caches and bounds.

Covers the four slow read endpoints assigned for performance + security
hardening: ``GET /search/discovery/categories`` and the three
``GET /suite/{home/recent,home/shared,search}`` feeds.

Guarantees asserted here:
* caching never changes the response (identical output cold vs warm vs
  invalidated),
* a cache hit does no backend work (the dominant cost is removed),
* keys are always per-viewer (no cross-user feed leakage / IDOR),
* result windows and query terms stay bounded even for direct callers.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from src.api.routes import search as search_route
from src.api.routes.suite import registry


@dataclass
class _Category:
    id: str
    name: str
    server_count: int = 0


class _CategoryBackend:
    def __init__(self, categories):
        self.categories = categories
        self.calls = 0

    def get_server_categories(self):
        self.calls += 1
        return list(self.categories)


class _ArtifactBackend:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.calls = 0

    def list_with_filters(self, filters=None, author_id=None, server_id=None):
        self.calls += 1
        return list(self.artifacts)


class _EmptyStash:
    def __init__(self):
        self.calls = 0

    def list_items(self, user_id, **kwargs):
        self.calls += 1
        return [], 0


class _EmptySum:
    def __init__(self):
        self.calls = 0

    def list_workbooks(self, user_id, **kwargs):
        self.calls += 1
        return [], 0


@pytest.fixture(autouse=True)
def _clear_caches():
    """Every test starts with empty caches so ordering cannot hide a bug."""
    search_route._clear_categories_cache()
    registry._clear_suite_feed_cache()
    yield
    search_route._clear_categories_cache()
    registry._clear_suite_feed_cache()


# ---------------------------------------------------------------------------
# Discovery categories
# ---------------------------------------------------------------------------


def test_categories_cache_identical_output_and_single_compute():
    backend = _CategoryBackend(
        [_Category("gaming", "Gaming", 7), _Category("music", "Music", 3)]
    )
    cold = search_route._server_categories(backend)
    warm = search_route._server_categories(backend)
    assert backend.calls == 1, "warm read must not recompute the GROUP BY"
    assert [(c.id, c.name, c.server_count) for c in warm] == [
        (c.id, c.name, c.server_count) for c in cold
    ]

    search_route._clear_categories_cache()
    after_invalidate = search_route._server_categories(backend)
    assert backend.calls == 2
    assert [(c.id, c.name, c.server_count) for c in after_invalidate] == [
        (c.id, c.name, c.server_count) for c in cold
    ]


def test_categories_cache_returns_deep_copies():
    """A caller mutating a returned row must not poison later cache hits."""
    backend = _CategoryBackend([_Category("gaming", "Gaming", 7)])
    first = search_route._server_categories(backend)
    first[0].server_count = 999
    second = search_route._server_categories(backend)
    assert second[0].server_count == 7
    assert backend.calls == 1


# ---------------------------------------------------------------------------
# Universal-home feeds
# ---------------------------------------------------------------------------


def _wire_feeds(monkeypatch, artifacts):
    """Swap the feed's backends for deterministic, call-counting fakes."""
    artifact_backend = _ArtifactBackend(artifacts)
    stash = _EmptyStash()
    summation = _EmptySum()
    monkeypatch.setattr(registry, "_get_manager", lambda: artifact_backend)
    monkeypatch.setattr(registry, "_suite_managers", lambda: (stash, summation))
    monkeypatch.setattr(registry, "_domain_available", lambda capability: True)
    monkeypatch.setattr(registry, "_member_server_ids", lambda user_id: [])
    monkeypatch.setattr(registry, "_shared_artifacts", lambda *a, **k: [])
    return artifact_backend, stash, summation


def test_recent_cache_hit_skips_all_backends(monkeypatch):
    artifact_backend, stash, summation = _wire_feeds(monkeypatch, [])
    cu = SimpleNamespace(user_id=42)
    first = asyncio.run(registry.list_recent_suite_items(limit=10, current_user=cu))
    calls_after_first = (
        artifact_backend.calls,
        stash.calls,
        summation.calls,
    )
    second = asyncio.run(registry.list_recent_suite_items(limit=10, current_user=cu))
    assert second == first
    assert (artifact_backend.calls, stash.calls, summation.calls) == calls_after_first


def test_feed_keys_are_per_viewer(monkeypatch):
    """A warmed feed for one user must never be served to another (IDOR)."""
    artifact_backend, _stash, _summation = _wire_feeds(monkeypatch, [])

    def _artifacts_for(user_id):
        # Echo the requested author so two viewers get distinguishable feeds.
        return [
            SimpleNamespace(
                id=user_id,
                artifact_type=SimpleNamespace(value="plexiscribe"),
                title=f"owner-{user_id}",
                summary=None,
                status=SimpleNamespace(value="completed"),
                conversation_id=None,
                channel_id=None,
                server_id=None,
                created_at=1,
                updated_at=1,
            )
        ]

    def list_with_filters(filters=None, author_id=None, server_id=None):
        artifact_backend.calls += 1
        return _artifacts_for(author_id)

    monkeypatch.setattr(artifact_backend, "list_with_filters", list_with_filters)

    alice = asyncio.run(
        registry.list_recent_suite_items(limit=5, current_user=SimpleNamespace(user_id=1))
    )
    bob = asyncio.run(
        registry.list_recent_suite_items(limit=5, current_user=SimpleNamespace(user_id=2))
    )
    assert [i["title"] for i in alice["items"]] == ["owner-1"]
    assert [i["title"] for i in bob["items"]] == ["owner-2"]
    assert all(key[0] in (1, 2) for key in registry._suite_feed_cache)


def test_limit_is_bounded_for_direct_callers(monkeypatch):
    """Internal callers cannot widen the result window past the HTTP cap."""
    _wire_feeds(monkeypatch, [])
    with pytest.raises(Exception):
        asyncio.run(
            registry.list_recent_suite_items(
                limit=10**9, current_user=SimpleNamespace(user_id=1)
            )
        )
    with pytest.raises(Exception):
        asyncio.run(
            registry.list_shared_suite_items(
                limit=-5, current_user=SimpleNamespace(user_id=1)
            )
        )


def test_search_query_term_is_bounded_in_cache_key(monkeypatch):
    _wire_feeds(monkeypatch, [])
    long_query = "a" * 5000
    asyncio.run(
        registry.search_suite(
            q=long_query, limit=5, current_user=SimpleNamespace(user_id=7)
        )
    )
    assert len(registry._suite_feed_cache) == 1
    key = next(iter(registry._suite_feed_cache))
    assert key[0] == 7
    assert len(key[3]) <= registry._MAX_SEARCH_LENGTH


def test_invalidation_drops_only_one_viewer(monkeypatch):
    _wire_feeds(monkeypatch, [])
    asyncio.run(
        registry.list_recent_suite_items(limit=5, current_user=SimpleNamespace(user_id=1))
    )
    asyncio.run(
        registry.list_recent_suite_items(limit=5, current_user=SimpleNamespace(user_id=2))
    )
    registry._clear_suite_feed_cache(user_id=1)
    assert all(key[0] != 1 for key in registry._suite_feed_cache)
    assert any(key[0] == 2 for key in registry._suite_feed_cache)


def test_cache_hit_is_faster_than_cold_compute(monkeypatch):
    """Deterministic timing proof: a deliberately slow backend is skipped."""
    artifact_backend, _stash, _summation = _wire_feeds(monkeypatch, [])

    def slow_list(filters=None, author_id=None, server_id=None):
        time.sleep(0.02)
        artifact_backend.calls += 1
        return []

    monkeypatch.setattr(artifact_backend, "list_with_filters", slow_list)
    cu = SimpleNamespace(user_id=99)

    t0 = time.perf_counter()
    asyncio.run(registry.list_recent_suite_items(limit=5, current_user=cu))
    cold_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    asyncio.run(registry.list_recent_suite_items(limit=5, current_user=cu))
    warm_ms = (time.perf_counter() - t0) * 1000

    assert warm_ms < cold_ms / 4, f"warm={warm_ms:.2f}ms cold={cold_ms:.2f}ms"
