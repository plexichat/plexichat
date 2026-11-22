"""Autocomplete answer caching bounds repeated prefix lookups.

The route memoizes recent (user, prefix, limit) answers so a typing burst does
not re-scan search history on every keystroke. Validation and the underlying
suggestion semantics are untouched.
"""

from types import SimpleNamespace

import pytest

import src.api.routes.search as search_route

pytestmark = pytest.mark.search


class _FakeSearch:
    def __init__(self):
        self.calls = []

    def get_search_suggestions(self, user_id, partial_query, limit):
        self.calls.append((user_id, partial_query, limit))
        return [f"{partial_query}:{i}" for i in range(limit)]


def test_repeated_prefix_is_computed_once():
    search_route._clear_suggestion_cache()
    mod = _FakeSearch()

    first = search_route._suggestions(mod, 1, "hel", 5)
    second = search_route._suggestions(mod, 1, "hel", 5)

    assert first == second
    assert len(mod.calls) == 1


def test_cache_is_keyed_by_user_prefix_and_limit():
    search_route._clear_suggestion_cache()
    mod = _FakeSearch()

    search_route._suggestions(mod, 1, "hel", 5)
    search_route._suggestions(mod, 2, "hel", 5)
    search_route._suggestions(mod, 1, "wor", 5)
    search_route._suggestions(mod, 1, "hel", 10)

    assert len(mod.calls) == 4


def test_clear_forces_recomputation():
    search_route._clear_suggestion_cache()
    mod = _FakeSearch()

    search_route._suggestions(mod, 7, "q", 3)
    search_route._clear_suggestion_cache()
    search_route._suggestions(mod, 7, "q", 3)

    assert len(mod.calls) == 2


def test_full_cache_evicts_one_lru_entry_not_every_users_answer(monkeypatch):
    """Hitting the cap must drop one stale entry, not wipe the whole cache.

    The old policy called ``_suggestion_cache.clear()`` at capacity, so a
    single new (user, prefix) threw away every other user's still-fresh answer.
    Under a typing burst that turns one miss into a wave of GROUP BYs. Evicting
    just the least-recently-used key keeps the remaining hot answers warm.
    """
    search_route._clear_suggestion_cache()
    monkeypatch.setattr(search_route, "_SUGGESTION_CACHE_TTL_SECONDS", 3600.0)
    monkeypatch.setattr(search_route, "_SUGGESTION_CACHE_MAX_ENTRIES", 4)
    mod = _FakeSearch()

    for uid in (1, 2, 3, 4):
        search_route._suggestions(mod, uid, "hel", 5)
    assert len(search_route._suggestion_cache) == 4
    assert len(mod.calls) == 4  # one lookup per distinct key

    # A fifth key at capacity costs exactly one backend query ...
    search_route._suggestions(mod, 5, "hel", 5)
    assert len(search_route._suggestion_cache) == 4
    assert len(mod.calls) == 5

    # ... and the still-live answers of other users survive the eviction.
    before = len(mod.calls)
    search_route._suggestions(mod, 2, "hel", 5)
    assert len(mod.calls) == before


def test_expired_entry_is_pruned_before_a_live_one(monkeypatch):
    """Expired keys must not occupy capacity or displace a fresh answer.

    Keys are per-user, so pruning is safe: the auth gate and rate limiter run
    before the cache is consulted, and an expired entry can only be recomputed
    on a later authorised request for that same user.
    """
    search_route._clear_suggestion_cache()
    now = [1000.0]
    monkeypatch.setattr(search_route, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(search_route, "_SUGGESTION_CACHE_TTL_SECONDS", 2.0)
    monkeypatch.setattr(search_route, "_SUGGESTION_CACHE_MAX_ENTRIES", 2)
    mod = _FakeSearch()

    search_route._suggestions(mod, 1, "old", 5)
    now[0] += 3.0  # first answer is now past the TTL
    search_route._suggestions(mod, 2, "live", 5)

    assert (1, "old", 5) not in search_route._suggestion_cache
    assert (2, "live", 5) in search_route._suggestion_cache
    before = len(mod.calls)
    search_route._suggestions(mod, 2, "live", 5)
    assert len(mod.calls) == before
