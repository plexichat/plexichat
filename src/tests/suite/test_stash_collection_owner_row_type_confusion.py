"""Stash collection ownership gates must reject a malformed owner row.

``StashManagerCollections.create_collection`` (parent-ownership check) and
``_can_grant_collection`` derive owner authority from ``int(row["owner_id"])
== int(user_id)`` on authoritative ``stash_collections`` rows. A
malformed-but-coercible value ("05", " 5 ", 5.9, True) lets a non-owner nest a
collection under someone else's parent or grant collection access to arbitrary
subjects -- neither path has a SQL owner backstop. Only a genuine integer id
from the row may authorize either.
"""

import pytest

from src.core.suite.stash._shared import StashPermissionError
from src.core.suite.stash.stash_manager_collections import StashManagerCollections

pytestmark = pytest.mark.unit


class _DB:
    def __init__(self, owner_value):
        self.owner_value = owner_value

    def fetch_one(self, query, params=None):
        return {"id": 200, "owner_id": self.owner_value, "server_id": None}

    def execute(self, query, params=None):
        pass


class _Service(StashManagerCollections):
    def __init__(self, owner_value):
        self._db = _DB(owner_value)

    def _generate_id(self):
        return 999

    def _audit(self, *args, **kwargs):
        pass

    def _can_administer_server(self, user_id, server_id):
        return False

    def _is_member(self, user_id, server_id):
        return False


def _create_nested_allowed(owner_value, user_id):
    service = _Service(owner_value)
    try:
        service.create_collection(user_id, "child", parent_id=200)
        return True
    except StashPermissionError:
        return False


def _grant_collection_allowed(owner_value, user_id):
    service = _Service(owner_value)
    collection = {"owner_id": owner_value, "server_id": None}
    try:
        service._can_grant_collection(collection, user_id)
        return True
    except StashPermissionError:
        return False


_CHECKS = {
    "create_nested": _create_nested_allowed,
    "grant_collection": _grant_collection_allowed,
}
_MALFORMED = [
    ("05", 5),
    (" 5 ", 5),
    (5.9, 5),
    ("5", 5),
    (True, 1),
]


@pytest.mark.parametrize("check", sorted(_CHECKS))
@pytest.mark.parametrize("owner_value, coerced_id", _MALFORMED)
def test_malformed_owner_row_does_not_grant(check, owner_value, coerced_id):
    assert _CHECKS[check](owner_value, coerced_id) is False, (
        f"malformed owner_id {owner_value!r} granted collection authority to "
        f"user {coerced_id} via {check}"
    )


def test_genuine_integer_owner_still_grants():
    assert _create_nested_allowed(5, 5) is True
    assert _grant_collection_allowed(5, 5) is True


def test_non_owner_integer_row_fails_closed():
    assert _create_nested_allowed(7, 5) is False
    assert _grant_collection_allowed(7, 5) is False
