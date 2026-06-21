"""Search - user + server search contracts."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(search_manager, auth_manager, pii_gen):
    """Seed a handful of users + servers so we can poke at lookup APIs."""
    from unittest.mock import patch
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        for i in range(5):
            auth_manager.register(
                username=f"searchuser{i}",
                email=pii_gen.email(),
                password="TestPass123!",
            )
    return search_manager


class TestSearchUsers:
    def test_search_users_returns_list(self, seeded):
        results = seeded.search_users(user_id=1, query="searchuser")
        assert isinstance(results, list)

    def test_search_users_paginated(self, seeded):
        page = seeded.search_users_page(user_id=1, query="searchuser", limit=2)
        assert page is not None


class TestSearchSuggestions:
    def test_get_search_suggestions(self, seeded):
        suggestions = seeded.get_search_suggestions(user_id=1, partial_query="sea")
        assert isinstance(suggestions, list)
