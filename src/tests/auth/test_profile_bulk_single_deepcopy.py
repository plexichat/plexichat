"""The cold-miss path of ``get_user_profiles_bulk`` snapshots a profile once,
not once per cache tier.

Both ``mem_cache_set`` (bounded process-local tier) and ``cache_set`` (shared
Valkey tier with a memory fallback) need an independent copy of the freshly
built profile: the original is returned to the caller, whose later mutation of
``badges``/``permissions`` must not poison either cached entry.  Neither cache
function mutates the object it is handed -- ``cache_set`` builds a fresh
serializable structure for Valkey and only stores the passed object on the
fallback path -- so a single snapshot can safely serve both.  The old code
called ``copy.deepcopy`` twice per profile on every cold miss.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import src.core.database as db_pkg
from src.core.auth.managers import profile as profile_mod
from src.core.auth.managers.profile import ProfileMixin
from src.core.database import invalidate_pattern

pytestmark = [pytest.mark.unit]


class _DB:
    def fetch_all(self, sql, params=()):
        return [
            {
                "id": uid,
                "username": f"u{uid}",
                "created_at": 1000 + uid,
                # A dict (not a JSON string) so ``ProfileMixin``'s protocol
                # stub ``_json_loads`` is not needed; these nested mutables are
                # exactly what the copy-safety check exercises.
                "permissions": {"read": True},
                "account_type": "user",
                "badges": ["seed"],
            }
            for uid in params
        ]


@pytest.fixture
def env():
    invalidate_pattern("user_profile:*")
    mixin = ProfileMixin()
    mixin._db = _DB()
    real_deepcopy = profile_mod.copy.deepcopy
    calls = {"n": 0}

    def _counting_deepcopy(obj):
        calls["n"] += 1
        return real_deepcopy(obj)

    with (
        patch.object(db_pkg, "valkey_available", return_value=True),
        patch.object(db_pkg, "cache_get_many", return_value={}),
        patch.object(db_pkg, "cache_set", return_value=True),
        patch.object(profile_mod.copy, "deepcopy", side_effect=_counting_deepcopy),
    ):
        yield mixin, calls
    invalidate_pattern("user_profile:*")


def test_cold_bulk_miss_snapshots_each_profile_once(env):
    mixin, calls = env
    ids = [701, 702, 703, 704, 705]
    result = mixin.get_user_profiles_bulk(ids)

    assert set(result) == {str(uid) for uid in ids}
    # One independent snapshot per profile serves both cache tiers.  Before the
    # fix this was 2 * len(ids): a second, identical ``deepcopy`` was computed
    # only for ``cache_set`` to immediately rebuild again on the Valkey write.
    assert calls["n"] == len(ids)


def test_one_snapshot_still_isolates_caller_from_both_tiers(env):
    """Copy-safety is preserved: mutating the returned profile changes no tier."""
    from src.core.database.cache.base import mem_cache_get

    mixin, _ = env
    uid = 701
    result = mixin.get_user_profiles_bulk([uid])
    profile = result[str(uid)]
    profile["badges"].append("poison")
    profile["permissions"]["x"] = True

    cached = mem_cache_get(f"user_profile:{uid}")
    assert cached is not None
    assert cached["badges"] == ["seed"]
    assert cached["permissions"] == {"read": True}
