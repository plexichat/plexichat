"""Authorization-adjacent search caches must not outlive the relationship.

``SearchAccessMixin`` memoizes each target user's server set and friend set so
user-search can compute mutual counts without one query per hit. Those sets are
authorization-adjacent: after a kick/ban/leave or an unfriend/block they must
not keep reporting a relationship that no longer exists for the full cache TTL.

Two distinct bugs are pinned here:

* the server memo used a private ``user:servers:{uid}`` namespace that no
  membership mutation ever cleared -- ``_invalidate_member_caches`` now drops
  it alongside the authoritative ``user_servers`` entry;
* the friend memo used a private ``user:friends:{uid}`` namespace instead of
  the authoritative ``user_friends`` key that every send/accept/remove/block
  path already invalidates -- it now shares that key (and value shape).
"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.database.cache.serialization import generate_cache_key
from src.core.search.manager.access import _user_friends_cache_key


class TestMembershipRevocationDropsSearchMemo:
    def test_invalidate_member_caches_deletes_search_server_key(self, monkeypatch):
        import src.core.database as dbmod
        import src.core.servers.manager.cache_ops as cache_ops
        import src.core.servers.manager.member_count_cache as member_count_cache

        deleted = []
        patterns = []
        monkeypatch.setattr(dbmod, "cache_delete", lambda key: deleted.append(key))
        monkeypatch.setattr(
            dbmod, "invalidate_pattern", lambda pat: patterns.append(pat)
        )
        monkeypatch.setattr(
            member_count_cache, "invalidate_member_count_cache", lambda: None
        )

        # The mixin only touches self for these two collaborator hooks.
        stub = SimpleNamespace(
            _roster_cache_prefix="srv_roster",
            _invalidate_roster_caches=lambda server_id: None,
            _invalidate_permission_caches=lambda server_id, user_id: None,
        )
        cache_ops.CacheOpsMixin._invalidate_member_caches(stub, 10, 42)

        assert "user:servers:42" in deleted
        # The authoritative entry is still dropped -- the fix is additive.
        assert "user_servers:42" in deleted


class TestFriendMemoSharesAuthoritativeKey:
    def test_search_key_matches_relationship_manager_cache_key(self):
        from src.core.relationships.manager.friends import _FRIEND_IDS_CACHE_PREFIX

        assert _user_friends_cache_key(7) == generate_cache_key(
            _FRIEND_IDS_CACHE_PREFIX, 7
        )

    def test_friend_memo_key_has_no_private_namespace(self):
        # Regression: the old key was ``user:friends:{uid}`` and was never
        # invalidated by any relationship mutation.
        assert _user_friends_cache_key(7) == "user_friends:int:7"
