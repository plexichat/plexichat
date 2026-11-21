"""Server-owner automod exemption must not grant on a malformed owner row.

``ExemptionMixin._is_exempt`` short-circuits to ``True`` when the projected
``srv_servers.owner_id`` equals the caller. That single in-memory comparison is
the whole gate -- there is no SQL backstop -- and it used to coerce the row
value with ``int()`` before testing equality.

A malformed-but-coercible owner value -- ``"05"``, a padded string, a float
``5.9`` or a bool ``True`` -- silently becomes a real user id, so a non-owner
is treated as the server owner and exempted from *every* automod rule. Only a
genuine integer id from the authoritative owner row may satisfy the check.
"""

import pytest

from src.core.automod.manager.exemptions import ExemptionMixin

pytestmark = pytest.mark.unit

_SERVER_ID = 900
_CHANNEL_ID = 901


class _DB:
    """Minimal DB: craft the owner row; no channel exemption, no role rows."""

    def __init__(self, owner_value):
        self.owner_value = owner_value

    def fetch_one(self, query, params=None):
        if "srv_servers" in query:
            return {"owner_id": self.owner_value}
        # automod_exemptions channel probe -> not exempt on that path
        return None

    def fetch_all(self, query, params=None):
        return []


class _Service(ExemptionMixin):
    def __init__(self, owner_value):
        self._db = _DB(owner_value)


def _verdict(owner_value, user_id):
    # user_role_ids=[] keeps the check on the owner branch only.
    return _Service(owner_value)._is_exempt(
        _SERVER_ID, user_id, _CHANNEL_ID, user_role_ids=[]
    )


@pytest.mark.parametrize(
    "owner_value, coerced_uid",
    [
        ("05", 5),  # leading-zero string: int() -> 5
        (" 5 ", 5),  # padded string
        (5.9, 5),  # float truncates to 5
        ("5", 5),  # plain numeric string
        (True, 1),  # bool coerces to 1
    ],
)
def test_malformed_owner_row_does_not_grant_exemption(owner_value, coerced_uid):
    # Pre-fix: int(owner_value) == coerced_uid is True -> exemption granted.
    assert _verdict(owner_value, coerced_uid) is False, (
        f"malformed owner_id {owner_value!r} coerced into an automod "
        f"exemption grant for user {coerced_uid}"
    )


def test_genuine_integer_owner_row_still_grants():
    """The gate still exempts the real owner (test is non-vacuous)."""
    assert _verdict(7, 7) is True


def test_non_owner_integer_row_fails_closed():
    assert _verdict(8, 7) is False
