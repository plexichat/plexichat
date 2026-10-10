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
        assert isinstance(page.results, list)


class TestSearchSuggestions:
    def test_get_search_suggestions(self, seeded):
        suggestions = seeded.get_search_suggestions(user_id=1, partial_query="sea")
        assert isinstance(suggestions, list)


class TestFriendLookupAgainstRelFriends:
    """Regression: friend lookups must not reference a non-existent `status`
    column on `rel_friends` (that column lives on `rel_friend_requests`).

    Previously `_get_user_friend_ids` / `_get_user_friends_map` filtered with
    `AND status = 'accepted'`, which raised `OperationalError: no such column:
    status` on every call. The exception was swallowed, so friend-based
    enrichment silently never ran.
    """

    @pytest.fixture
    def seeded_friends(self, search_manager, auth_manager, pii_gen):
        # rel_friends has FK constraints to auth_users, so the friend
        # rows must reference real registered users.
        from unittest.mock import patch

        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            u1 = auth_manager.register(
                username="friendseed1",
                email=pii_gen.email(),
                password="TestPass123!",
            )
            u2 = auth_manager.register(
                username="friendseed2",
                email=pii_gen.email(),
                password="TestPass123!",
            )
            u3 = auth_manager.register(
                username="friendseed3",
                email=pii_gen.email(),
                password="TestPass123!",
            )

        uid1, uid2, uid3 = u1.id, u2.id, u3.id
        now = search_manager._get_timestamp()

        search_manager._db.execute(
            "INSERT INTO rel_friends (id, user_id, friend_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (900000000000000011, uid1, uid2, now),
        )
        search_manager._db.execute(
            "INSERT INTO rel_friends (id, user_id, friend_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (900000000000000012, uid2, uid1, now),
        )
        search_manager._db.execute(
            "INSERT INTO rel_friends (id, user_id, friend_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (900000000000000013, uid1, uid3, now),
        )
        return uid1, uid2, uid3

    def test_get_user_friend_ids_no_status_column(self, search_manager, seeded_friends):
        uid1, uid2, uid3 = seeded_friends
        assert search_manager._get_user_friend_ids(uid1) == {uid2, uid3}
        assert search_manager._get_user_friend_ids(uid2) == {uid1}

    def test_get_user_friends_map_no_status_column(
        self, search_manager, seeded_friends
    ):
        uid1, uid2, uid3 = seeded_friends
        mapping = search_manager._get_user_friends_map([uid1, uid2, uid3])
        assert mapping[uid1] == {uid2, uid3}
        assert mapping[uid2] == {uid1}
        assert mapping[uid3] == set()
