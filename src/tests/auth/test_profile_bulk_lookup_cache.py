"""Regression for repeated username/display-name resolution in listings.

Every listing (message pages, guild members, search results, DMs, ban/grant
lists) resolves the username/avatar display data through
``ProfileMixin.get_user_profiles_bulk``.  Before the bounded L1 tier that
helper re-read the shared cache once per user on each page, and skipped the
cache entirely when Valkey was down -- re-querying ``auth_users`` for every
listing request.  These tests pin the measured reduction and the coherence
contract:

* a warm listing resolves every id from the bounded L1 (no Valkey GET, no SQL);
* invalidation on a profile edit (``cache_delete`` / ``invalidate_pattern``)
  drops the L1 entry, so a stale username cannot survive a write;
* the TTL bounds cross-worker staleness and the entry count is bounded by the
  shared process-local tier.

The real bulk resolver and key/invalidation helpers stay on the path; only the
database and cache backend are replaced by counting fakes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import src.core.database as db_pkg
from src.core.auth.managers import profile as profile_mod
from src.core.auth.managers.profile import (
    PROFILE_BULK_L1_TTL_SECONDS,
    ProfileMixin,
    get_profile_bulk_l1_stats,
    reset_profile_bulk_l1_stats,
)
from src.core.database import invalidate_pattern
from src.core.database.cache.operations import cache_delete

pytestmark = [pytest.mark.unit]

USER_IDS = (701, 702, 703)


def _row(uid: int) -> dict:
    return {
        "id": uid,
        "username": f"user_{uid}",
        "created_at": 1000 + uid,
        "permissions": "{}",
        "account_type": "user",
        "badges": None,
    }


class _CountingDB:
    def __init__(self):
        self.bulk_queries = []

    def fetch_all(self, sql, params=()):
        self.bulk_queries.append((sql, tuple(params)))
        return [_row(uid) for uid in params]


@pytest.fixture(autouse=True)
def _isolated_l1():
    """Clear the shared ``user_profile:*`` namespace around every test."""
    invalidate_pattern("user_profile:*")
    reset_profile_bulk_l1_stats()
    yield
    invalidate_pattern("user_profile:*")
    reset_profile_bulk_l1_stats()


@pytest.fixture
def env():
    fake_db = _CountingDB()
    mixin = ProfileMixin()
    mixin._db = fake_db

    valkey_gets = []

    def _cache_get(key):
        valkey_gets.append(key)
        return None  # cold shared cache: force the SQL fallback

    def _cache_set(key, value, ttl=None):
        return True

    with (
        patch.object(db_pkg, "valkey_available", return_value=True),
        patch.object(db_pkg, "cache_get", side_effect=_cache_get),
        patch.object(db_pkg, "cache_set", side_effect=_cache_set),
    ):
        yield mixin, fake_db, valkey_gets


def test_warm_listing_resolves_profiles_without_repeated_lookups(env):
    """Two identical listings pay for resolution once, not twice."""
    mixin, db, valkey_gets = env

    first = mixin.get_user_profiles_bulk(list(USER_IDS))
    first_queries, first_gets = len(db.bulk_queries), len(valkey_gets)
    second = mixin.get_user_profiles_bulk(list(USER_IDS))

    assert first == second
    assert set(first) == {str(uid) for uid in USER_IDS}
    # Cold: one bulk SQL query and one shared-cache GET per distinct id.
    assert first_queries == 1
    assert first_gets == len(USER_IDS)
    # Warm: the second listing performs NO further SQL or Valkey round-trip.
    assert len(db.bulk_queries) == first_queries
    assert len(valkey_gets) == first_gets
    stats = get_profile_bulk_l1_stats()
    assert stats["misses"] == len(USER_IDS)
    assert stats["hits"] == len(USER_IDS)
    assert stats["valkey_gets"] == len(USER_IDS)


def test_profile_edit_invalidation_drops_warm_entry(env):
    """A username change must be visible on the very next listing."""
    mixin, db, _ = env
    mixin.get_user_profiles_bulk(list(USER_IDS))
    assert len(db.bulk_queries) == 1

    # Same hooks the mutation paths use (update_user, deletion, badges, admin).
    cache_delete("user_profile:702")
    mixin.get_user_profiles_bulk([702])

    # Only the edited id was re-resolved; the other warm ids were untouched.
    assert len(db.bulk_queries) == 2
    assert db.bulk_queries[-1][1] == (702,)

    invalidate_pattern("user_profile:*")
    mixin.get_user_profiles_bulk([701, 703])
    assert len(db.bulk_queries) == 3


def test_l1_ttl_bounds_cross_worker_staleness(env, monkeypatch):
    """An expired L1 entry is never served; the resolver falls back."""
    mixin, db, _ = env
    monkeypatch.setattr(profile_mod, "PROFILE_BULK_L1_TTL_SECONDS", 0)

    mixin.get_user_profiles_bulk([701])
    assert len(db.bulk_queries) == 1
    mixin.get_user_profiles_bulk([701])
    assert len(db.bulk_queries) == 2  # ttl=0 expired between reads


def test_l1_is_bounded_by_the_shared_mem_tier(env):
    """Flooding distinct ids cannot grow the process-local tier without limit."""
    mixin, _, _ = env
    import src.core.database.cache.base as base

    invalidate_pattern("user_profile:*")
    # More ids than the shared tier's hard entry ceiling.
    mixin.get_user_profiles_bulk(list(range(1, base._MEM_CACHE_MAX_SIZE + 200)))
    profile_keys = [k for k in base._mem_cache if k.startswith("user_profile:")]
    assert len(profile_keys) <= base._MEM_CACHE_MAX_SIZE
    assert PROFILE_BULK_L1_TTL_SECONDS <= 30
