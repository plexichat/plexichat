"""PlexiSum workbook-owner gate must reject a malformed owner row.

``SumManagerOperations._check_range_permissions`` and
``_check_structural_permission`` derive the workbook-owner bypass from
``int(workbook["owner_id"]) == int(user_id)`` on the authoritative
``sum_workbooks.owner_id`` row. That in-memory comparison is the whole gate:
passing it returns early and skips every ``read_only`` range / protected-band
check, i.e. it grants full workbook-edit authority.

A malformed-but-coercible owner value -- ``"05"``, a padded string, ``5.9``
or a bool -- silently becomes a real user id, so a non-owner is treated as the
owner and may edit cells / delete rows inside protected ranges. Only a genuine
integer id from the authoritative owner row may satisfy either check.
"""

import pytest

from src.core.suite.sum._shared import SumPermissionError
from src.core.suite.sum.manager_operations import SumManagerOperations

pytestmark = pytest.mark.unit

_WORKBOOK_ID = 777
_SHEET_ID = 11
_PROTECTED = [
    {"row_start": 0, "row_end": 10, "column_start": 0, "column_end": 10}
]


class _DB:
    """Minimal DB: hand back the crafted owner row on the workbook probe."""

    def __init__(self, owner_value):
        self.owner_value = owner_value

    def fetch_one(self, query, params=None):
        if "sum_workbooks" in query:
            return {"owner_id": self.owner_value}
        return None

    def fetch_all(self, query, params=None):
        if "sum_range_permissions" in query:
            return list(_PROTECTED)
        return []


def _manager(owner_value):
    mgr = SumManagerOperations()
    mgr._db = _DB(owner_value)
    return mgr


def _range_allows(owner_value, user_id):
    """True when the caller may edit a cell inside a protected range."""
    try:
        _manager(owner_value)._check_range_permissions(
            _WORKBOOK_ID,
            _SHEET_ID,
            [{"row": 1, "column": 1}],
            user_id,
        )
        return True
    except SumPermissionError:
        return False


def _structural_allows(owner_value, user_id):
    """True when the caller may delete a row inside a protected band."""
    try:
        _manager(owner_value)._check_structural_permission(
            _WORKBOOK_ID,
            str(_SHEET_ID),
            "row",
            2,
            1,
            False,
            user_id,
        )
        return True
    except SumPermissionError:
        return False


# Pre-fix: int(owner_value) == coerced_id -> owner bypass -> protected cell
# edit / structural delete silently granted to a non-owner.
_MALFORMED = [
    ("05", 5),  # leading-zero string: int() -> 5
    (" 5 ", 5),  # padded string
    (5.9, 5),  # float truncates to 5
    ("5", 5),  # plain numeric string
    (True, 1),  # bool coerces to 1
]


@pytest.mark.parametrize("owner_value, coerced_id", _MALFORMED)
def test_malformed_owner_row_does_not_grant_range_edit(owner_value, coerced_id):
    assert _range_allows(owner_value, coerced_id) is False, (
        f"malformed owner_id {owner_value!r} was coerced into workbook-owner "
        f"edit authority for user {coerced_id}"
    )


@pytest.mark.parametrize("owner_value, coerced_id", _MALFORMED)
def test_malformed_owner_row_does_not_grant_structural_edit(owner_value, coerced_id):
    assert _structural_allows(owner_value, coerced_id) is False, (
        f"malformed owner_id {owner_value!r} was coerced into workbook-owner "
        f"structural authority for user {coerced_id}"
    )


def test_genuine_integer_owner_still_grants_range_edit():
    """Non-vacuous: the real owner still bypasses protected ranges."""
    assert _range_allows(5, 5) is True


def test_genuine_integer_owner_still_grants_structural_edit():
    """Non-vacuous: the real owner still bypasses protected bands."""
    assert _structural_allows(5, 5) is True


def test_non_owner_integer_row_fails_closed():
    assert _range_allows(7, 5) is False
    assert _structural_allows(7, 5) is False
