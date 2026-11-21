"""Server-scoped search visibility must not grant on a malformed member row.

``_scope_allows_viewer`` answers whether the caller belongs to the server whose
roster a ``?server_id=X`` user search would expose. Its docstring promises that
"malformed scope data fails closed" so a guessed server id is not a
roster-enumeration oracle. The scope set is the live ``srv_members.user_id``
projection for that server (a broad ``SELECT user_id FROM srv_members WHERE
server_id = ?`` with no id filter), and the gate coerces every value with
``int()`` before the membership test.

The ``try/except`` only catches values that cannot be ``int()``-ed at all. A
malformed-but-coercible row -- ``"05"``, a padded string, a float ``5.9``, or
``True`` -- silently becomes the caller's id and is admitted, granting roster
visibility the row does not prove. Only genuine integer ids from live rows may
satisfy the gate.
"""

import pytest

from src.core.search.manager.user_search import UserSearchMixin

pytestmark = pytest.mark.unit

_SCOPE = UserSearchMixin._scope_allows_viewer


@pytest.mark.parametrize(
    "malformed_uid, coerced_viewer",
    [
        ("05", 5),  # leading zero: int() -> 5, not the stored identity
        (" 5 ", 5),  # padded string
        (5.9, 5),  # float truncates to 5
        (True, 1),  # bool coerces to 1
        ("1", 1),  # numeric string
    ],
)
def test_malformed_member_row_does_not_grant_scope(malformed_uid, coerced_viewer):
    """A malformed srv_members.user_id must never satisfy the viewer gate."""
    assert _SCOPE(coerced_viewer, {malformed_uid}) is False, (
        f"malformed member id {malformed_uid!r} coerced into a grant for "
        f"viewer {coerced_viewer}"
    )


def test_genuine_integer_member_row_still_grants():
    """The gate still admits a real member (test is non-vacuous)."""
    assert _SCOPE(5, {5, 7, 9}) is True


def test_non_member_and_missing_scope_fail_closed():
    assert _SCOPE(6, {5, 7, 9}) is False
    assert _SCOPE(5, set()) is False
