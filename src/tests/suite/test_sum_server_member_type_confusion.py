"""PlexiSum server-member ids must be genuine ints in the authorized audience.

``SumManagerBase._authorized_user_ids`` derives the set of users who may view a
server-scoped workbook (and therefore who receive its WORKBOOK_UPDATE realtime
event) by expanding ``servers.get_member_user_ids(server_id)``::

    for member_id in self._servers.get_member_user_ids(int(server_id)) or []:
        ids[int(member_id)] = None

``get_member_user_ids`` returns the raw ``srv_members.user_id`` projection
(``SELECT user_id FROM srv_members WHERE server_id = ?``) with no type
filtering, so a malformed-but-coercible value -- ``"07"``, ``" 8 "``, ``7.9``,
``True`` -- is laundered by ``int()`` into a real user id.  That admits the
*wrong* principal to the workbook's authorized-viewer audience even though no
genuine membership for that id was ever proven.  Only an exact integer id from
the authoritative roster may attest a viewer; ``type()`` also rejects bool.

The companion grant-subject hardening (891a80f8) fixed the *grant* side of this
same audience; this pins the *server-member* side.
"""

import pytest

from src.core.suite.sum.manager_base import SumManagerBase

pytestmark = pytest.mark.unit

_OWNER_ID = 100
_VALID_MEMBER = 500
_WORKBOOK = {
    "id": 777,
    "owner_id": _OWNER_ID,
    "scope": "server",
    "server_id": 42,
}


class _DB:
    """Minimal DB: no grants; the member rows come from the servers stub."""

    def fetch_all(self, query, params=None):
        return []

    def fetch_one(self, query, params=None):
        return None

    def execute(self, query, params=None):
        return None


class _Servers:
    def __init__(self, member_rows):
        self.member_rows = member_rows

    def get_member_user_ids(self, server_id):
        return list(self.member_rows)


def _authorized_user_ids(member_rows):
    mgr = object.__new__(SumManagerBase)
    mgr._db = _DB()
    mgr._servers = _Servers(member_rows)
    return mgr._authorized_user_ids(_WORKBOOK)


# Pre-fix: int(malformed) -> coerced_id -> that user joins the viewer audience.
_MALFORMED = [
    ("07", 7),  # leading-zero string: int() -> 7
    (" 8 ", 8),  # padded string -> 8
    (7.9, 7),  # float truncates to 7
    ("5", 5),  # plain numeric string -> 5
    (True, 1),  # bool coerces to 1
]


@pytest.mark.parametrize("member_value, coerced_id", _MALFORMED)
def test_malformed_server_member_does_not_join_viewer_audience(
    member_value, coerced_id
):
    ids = _authorized_user_ids(
        [
            _VALID_MEMBER,  # a genuine-int member must still be admitted
            member_value,  # the malformed roster row under attack
        ]
    )
    assert coerced_id not in ids, (
        f"malformed srv_members.user_id {member_value!r} was int()-coerced "
        f"into admitting user {coerced_id} to the WORKBOOK_UPDATE audience"
    )
    # Non-vacuous: the legitimate member and the owner must still be present.
    assert _VALID_MEMBER in ids
    assert _OWNER_ID in ids


def test_genuine_integer_server_member_still_joins_audience():
    ids = _authorized_user_ids([500])
    assert 500 in ids
    assert _OWNER_ID in ids
