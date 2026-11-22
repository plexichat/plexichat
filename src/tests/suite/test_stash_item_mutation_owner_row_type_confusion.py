"""Stash item restore must reject a malformed owner row.

``ItemMutationMixin.restore_item`` authorizes the lifecycle restore purely from
``int(row["owner_id"]) == int(user_id)`` on an authoritative ``stash_items``
row. A malformed-but-coercible value ("05", " 5 ", 5.9, True) lets a non-owner
restore another user's soft-deleted item -- there is no SQL backstop on the
restore UPDATE. Only a genuine integer id from the row may authorize it.
"""

import pytest

from src.core.suite.stash._shared import StashPermissionError
from src.core.suite.stash.stash_manager_items.mutations import ItemMutationMixin

pytestmark = pytest.mark.unit


class _DB:
    def __init__(self, owner_value):
        self.owner_value = owner_value
        self.restored = False

    def fetch_one(self, query, params=None):
        return {"id": 100, "owner_id": self.owner_value, "server_id": None}

    def execute(self, query, params=None):
        self.restored = True


class _Service(ItemMutationMixin):
    def __init__(self, owner_value):
        self._db = _DB(owner_value)

    def _audit(self, *args, **kwargs):
        pass

    def get_item(self, user_id, item_id):
        return {"id": str(item_id)}


def _restore_allowed(owner_value, user_id):
    service = _Service(owner_value)
    try:
        service.restore_item(user_id, 100)
        return True
    except StashPermissionError:
        return False


_MALFORMED = [
    ("05", 5),
    (" 5 ", 5),
    (5.9, 5),
    ("5", 5),
    (True, 1),
]


@pytest.mark.parametrize("owner_value, coerced_id", _MALFORMED)
def test_malformed_owner_row_does_not_grant_restore(owner_value, coerced_id):
    assert _restore_allowed(owner_value, coerced_id) is False, (
        f"malformed owner_id {owner_value!r} granted item restore to user {coerced_id}"
    )


def test_genuine_integer_owner_still_restores():
    assert _restore_allowed(5, 5) is True


def test_non_owner_integer_row_fails_closed():
    assert _restore_allowed(7, 5) is False
