"""Autocomplete answer caching bounds repeated prefix lookups.

The route memoizes recent (user, prefix, limit) answers so a typing burst does
not re-scan search history on every keystroke. Validation and the underlying
suggestion semantics are untouched.
"""

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
