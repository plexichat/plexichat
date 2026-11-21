"""Regression: a caller must not be able to poison the bulk profile cache.

``ProfileMixin.get_user_profiles_bulk`` is the shared username/display resolver
for every listing. It used to hand out the very dict objects it stored in the
bounded process-local L1 tier and in the shared Valkey cache. A caller that
mutated a returned profile -- or a nested ``badges``/``permissions`` value --
silently rewrote the cached entry seen by every later reader until its TTL.
This pins the copy-on-read / copy-on-write contract (the same one already used
for the cached emoji catalogue).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import src.core.database as db_pkg
from src.core.auth.managers.profile import ProfileMixin
from src.core.database import invalidate_pattern

pytestmark = [pytest.mark.unit]

UID = 701


def _row(uid: int) -> dict:
    return {
        "id": uid,
        "username": f"user_{uid}",
        "created_at": 1000 + uid,
        "permissions": "{}",
        "account_type": "user",
        "badges": '["wave"]',
    }


class _CountingDB:
    def __init__(self):
        self.queries = []

    def fetch_all(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        return [_row(uid) for uid in params]


@pytest.fixture(autouse=True)
def _isolated_l1():
    invalidate_pattern("user_profile:*")
    yield
    invalidate_pattern("user_profile:*")


def _mixin(db):
    m = ProfileMixin()
    m._db = db
    # The real manager supplies this via its base class; a bare Protocol
    # instance does not, so wire the trivial JSON decoder the resolver needs.
    m._json_loads = lambda data: json.loads(data) if data else None
    return m


def test_l1_hit_is_not_poisoned_by_caller_mutation():
    db = _CountingDB()
    mixin = _mixin(db)
    with patch.object(db_pkg, "valkey_available", return_value=False):
        first = mixin.get_user_profiles_bulk([UID])
        profile = first[str(UID)]
        profile["username"] = "poisoned"
        profile["badges"].append("evil")
        profile["permissions"]["admin"] = True

        second = mixin.get_user_profiles_bulk([UID])

    cached = second[str(UID)]
    assert cached["username"] == f"user_{UID}"
    assert cached["badges"] == ["wave"]
    assert cached["permissions"] == {}
    # The second read was an L1 hit, not a fresh SQL query.
    assert len(db.queries) == 1


def test_shared_cache_entry_is_not_poisoned_by_caller_mutation():
    db = _CountingDB()
    mixin = _mixin(db)
    shared = {}

    def _cache_set(key, value, ttl=None):
        shared[key] = value
        return True

    with (
        patch.object(db_pkg, "valkey_available", return_value=True),
        patch.object(db_pkg, "cache_get_many", side_effect=lambda keys: {}),
        patch.object(db_pkg, "cache_set", side_effect=_cache_set),
    ):
        first = mixin.get_user_profiles_bulk([UID])
        profile = first[str(UID)]
        profile["username"] = "poisoned"
        profile["badges"].append("evil")
        profile["permissions"]["admin"] = True

        # Capture the shared entry, then drop the process-local L1 to simulate
        # a different worker that only has the shared cache available.
        shared_entry = dict(shared[f"user_profile:{UID}"])
        invalidate_pattern("user_profile:*")

        second = mixin.get_user_profiles_bulk([UID])

    assert shared_entry["username"] == f"user_{UID}"
    assert shared_entry["badges"] == ["wave"]
    assert shared_entry["permissions"] == {}
    assert second[str(UID)]["username"] == f"user_{UID}"
    assert second[str(UID)]["badges"] == ["wave"]
    assert second[str(UID)]["permissions"] == {}
