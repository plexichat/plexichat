"""Stash item/collection owner gates must reject a malformed owner row.

``StashManagerBase`` derives stash permissions and storage/media-reference
exposure from ``int(<row>["owner_id"]) == int(user_id)`` on authoritative
``stash_items`` / ``stash_collections`` rows. Those in-memory comparisons are
the whole gate -- there is no SQL backstop. A malformed-but-coercible value
-- ``"05"``, a padded string, ``5.9`` or a bool -- silently becomes a real
user id, so a non-owner is treated as the owner and may read/edit/share other
users' stash items and pull their opaque storage references. Only a genuine
integer id from an authoritative row may satisfy any of these checks.
"""

import pytest

from src.core.suite.stash._shared import StashPermissionError
from src.core.suite.stash.stash_manager_base import StashManagerBase

pytestmark = pytest.mark.unit


class _DB:
    def fetch_one(self, query, params=None):
        return None

    def fetch_all(self, query, params=None):
        return []


class _Service(StashManagerBase):
    """Neutralize grant paths so only the owner comparison can authorize."""

    def __init__(self, db):
        self._db = db

    def _grant_permissions(self, item_id, user_id, permission):
        return False

    def _collection_granted(self, item, user_id, permission):
        return False

    def _can_administer_server(self, user_id, server_id):
        return False

    def _is_member(self, user_id, server_id):
        return False


def _item(owner_value):
    return {
        "id": 100,
        "owner_id": owner_value,
        "scope": "personal",
        "server_id": None,
    }


def _authorize_allows(owner_value, user_id):
    try:
        _Service(_DB())._authorize(_item(owner_value), user_id, "view_metadata")
        return True
    except StashPermissionError:
        return False


def _expose_allows(owner_value, user_id):
    return _Service(_DB())._can_expose_storage(_item(owner_value), user_id)


def _expose_batch_allows(owner_value, user_id):
    result = _Service(_DB())._can_expose_storage_batch([_item(owner_value)], user_id)
    return result[100]


def _validate_collection_allows(owner_value, user_id):
    class CollectionDB(_DB):
        def fetch_one(self, query, params=None):
            return {"owner_id": owner_value, "server_id": None}

    try:
        _Service(CollectionDB())._validate_collection(user_id, 200, "personal", None)
        return True
    except StashPermissionError:
        return False


# Pre-fix: int(owner_value) == coerced_id -> owner -> every gate grants.
_MALFORMED = [
    ("05", 5),  # leading-zero string: int() -> 5
    (" 5 ", 5),  # padded string
    (5.9, 5),  # float truncates to 5
    ("5", 5),  # plain numeric string
    (True, 1),  # bool coerces to 1
]


@pytest.mark.parametrize("check", ["authorize", "expose", "expose_batch", "collection"])
@pytest.mark.parametrize("owner_value, coerced_id", _MALFORMED)
def test_malformed_owner_row_does_not_grant(check, owner_value, coerced_id):
    checks = {
        "authorize": _authorize_allows,
        "expose": _expose_allows,
        "expose_batch": _expose_batch_allows,
        "collection": _validate_collection_allows,
    }
    assert checks[check](owner_value, coerced_id) is False, (
        f"malformed owner_id {owner_value!r} was coerced into stash-owner "
        f"authority for user {coerced_id} via {check}"
    )


def test_genuine_integer_owner_still_grants():
    """Non-vacuous: the real owner still passes every gate."""
    assert _authorize_allows(5, 5) is True
    assert _expose_allows(5, 5) is True
    assert _expose_batch_allows(5, 5) is True
    assert _validate_collection_allows(5, 5) is True


def test_non_owner_integer_row_fails_closed():
    assert _authorize_allows(7, 5) is False
    assert _expose_allows(7, 5) is False
    assert _expose_batch_allows(7, 5) is False
    assert _validate_collection_allows(7, 5) is False
