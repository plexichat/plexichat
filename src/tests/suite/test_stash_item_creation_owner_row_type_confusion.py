"""Stash item grant-list disclosure must reject a malformed owner row.

``ItemCreationMixin.get_item`` / ``_project_item_after_mutation`` decide whether
to attach the owner-only ``grants`` list from ``int(row["owner_id"]) ==
int(user_id)`` on an authoritative ``stash_items`` row. A malformed-but-coercible
value ("05", " 5 ", 5.9, True) makes a non-owner look like the owner and leaks
every grant (subject ids, permissions, expiry) for that item. Only a genuine
integer id from the row may authorize disclosure.
"""

import pytest

from src.core.suite.stash.stash_manager_items.creation import ItemCreationMixin

pytestmark = pytest.mark.unit


class _DB:
    def __init__(self, owner_value):
        self.owner_value = owner_value

    def fetch_one(self, query, params=None):
        return {
            "id": 100,
            "owner_id": self.owner_value,
            "scope": "personal",
            "server_id": None,
        }


class _Service(ItemCreationMixin):
    def __init__(self, owner_value):
        self._db = _DB(owner_value)

    def _authorize(self, *args, **kwargs):
        pass

    def _row_to_item(self, row, include_storage=False):
        return {"id": str(row["id"])}

    def _can_expose_storage(self, row, user_id):
        return False

    def _can_administer_server(self, user_id, server_id):
        return False

    def _item_grants(self, item_id):
        return ["SENSITIVE-GRANT"]


def _grants_visible(method, owner_value, user_id):
    item = getattr(_Service(owner_value), method)(user_id, 100)
    return "grants" in item


_METHODS = ["get_item", "_project_item_after_mutation"]
_MALFORMED = [
    ("05", 5),
    (" 5 ", 5),
    (5.9, 5),
    ("5", 5),
    (True, 1),
]


@pytest.mark.parametrize("method", _METHODS)
@pytest.mark.parametrize("owner_value, coerced_id", _MALFORMED)
def test_malformed_owner_row_does_not_disclose_grants(method, owner_value, coerced_id):
    assert _grants_visible(method, owner_value, coerced_id) is False, (
        f"malformed owner_id {owner_value!r} disclosed owner-only grants to "
        f"user {coerced_id} via {method}"
    )


def test_genuine_integer_owner_still_sees_grants():
    assert _grants_visible("get_item", 5, 5) is True
    assert _grants_visible("_project_item_after_mutation", 5, 5) is True


def test_non_owner_integer_row_fails_closed():
    assert _grants_visible("get_item", 7, 5) is False
    assert _grants_visible("_project_item_after_mutation", 7, 5) is False
