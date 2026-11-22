"""Bulk owner-permission resolution must not grant on a malformed owner row.

``PermissionOpsMixin.get_permissions_bulk`` resolves a server's permission set
and marks the caller as owner when ``srv_servers.owner_id`` equals their id.
That owner flag makes ``calculate_base_permissions`` grant every server
permission, so it is a permission set with security consequences. It used to
coerce the projected ``owner_id`` with ``int()`` before the equality test.

A malformed-but-coercible row -- ``"05"``, a padded string, a float ``5.9`` or
a bool ``True`` -- silently becomes the caller's id and is treated as an
owner, granting full permissions for a server the caller does not own. Only a
genuine integer owner id from the authoritative row may satisfy the check.
"""

import pytest

from src.core.servers.manager.permission_ops import PermissionOpsMixin

pytestmark = pytest.mark.unit

_VIEW_AUDIT = "server.view_audit_log"


class _DB:
    """Return the crafted srv_servers row; no roles, so only owner can grant."""

    def __init__(self, owner_id):
        self.owner_id = owner_id

    def fetch_all(self, query, params=None):
        q = " ".join(query.split())
        if "srv_servers s" in q and "srv_members m" in q:
            return [{"server_id": 10, "owner_id": self.owner_id, "member_id": 100}]
        if "srv_member_roles mr" in q:
            return []
        raise AssertionError(f"unexpected query: {q}")


def _perms(owner_id, user_id):
    class _Mgr(PermissionOpsMixin):
        _db = _DB(owner_id)

    return _Mgr().get_permissions_bulk(user_id, [10]).get(10, {})


@pytest.mark.parametrize(
    "malformed_owner, coerced_user",
    [
        ("05", 5),  # leading zero: int() -> 5
        (" 5 ", 5),  # padded string
        (5.9, 5),  # float truncates to 5
        (True, 1),  # bool coerces to 1
    ],
)
def test_malformed_owner_row_does_not_grant_owner_permissions(
    malformed_owner, coerced_user
):
    perms = _perms(malformed_owner, coerced_user)
    assert perms.get(_VIEW_AUDIT, False) is False, (
        f"malformed owner_id {malformed_owner!r} coerced into an owner grant "
        f"for user {coerced_user}"
    )


def test_genuine_owner_still_grants():
    """A genuine integer owner id still grants (test is non-vacuous)."""
    assert _perms(5, 5).get(_VIEW_AUDIT, False) is True


def test_genuine_non_owner_denied():
    assert _perms(6, 5).get(_VIEW_AUDIT, False) is False
