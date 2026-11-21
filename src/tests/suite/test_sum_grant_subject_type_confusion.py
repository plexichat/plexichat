"""PlexiSum grant SUBJECT ids must be genuine ints when read back from a row.

``SumManagerBase._authorized_user_ids`` derives the set of users who may view a
workbook (and therefore who receive its WORKBOOK_UPDATE realtime event) from the
``sum_grants.subject_id`` column::

    ids[int(row["subject_id"])] = None

That in-memory ``int()`` coercion is the whole gate for admitting a grant holder
to the authorized-viewer list. A malformed-but-coercible subject value --
``"07"``, ``7.9``, a padded string, or a bool -- silently becomes a real user
id, so a corrupted / hand-inserted grant row can admit the *wrong* principal to
the workbook's authorized audience even though no valid grant was ever made to
them. Only a genuine integer id from the authoritative grant row may attest a
subject; ``type()`` also rejects bool (``True == 1``).

The companion stash owner-row hardening fixed the *owner* side of the same
confusion; this pins the grant *subject* side.
"""

import pytest

from src.core.suite.sum.manager_base import SumManagerBase

pytestmark = pytest.mark.unit

_OWNER_ID = 100
_VALID_GRANTEE = 200
_WORKBOOK = {
    "id": 777,
    "owner_id": _OWNER_ID,
    "scope": "personal",
    "server_id": None,
}


class _DB:
    """Minimal DB: hand back crafted ``sum_grants`` rows on the grant probe."""

    def __init__(self, grant_rows):
        self.grant_rows = grant_rows

    def fetch_all(self, query, params=None):
        if "sum_grants" in query:
            return list(self.grant_rows)
        return []

    def fetch_one(self, query, params=None):
        return None

    def execute(self, query, params=None):
        return None


def _authorized_user_ids(grant_rows):
    mgr = object.__new__(SumManagerBase)
    mgr._db = _DB(grant_rows)
    mgr._servers = None  # personal workbook: no server-member expansion
    return mgr._authorized_user_ids(_WORKBOOK)


# Pre-fix: int(malformed) -> coerced_id -> that user is admitted as a grantee.
_MALFORMED = [
    ("07", 7),  # leading-zero string: int() -> 7
    (" 8 ", 8),  # padded string
    (7.9, 7),  # float truncates to 7
    ("5", 5),  # plain numeric string
    (True, 1),  # bool coerces to 1
]


@pytest.mark.parametrize("subject_value, coerced_id", _MALFORMED)
def test_malformed_grant_subject_does_not_authorize_wrong_user(
    subject_value, coerced_id
):
    ids = _authorized_user_ids(
        [
            {"subject_id": _VALID_GRANTEE},  # a legitimate, genuine-int grant
            {"subject_id": subject_value},  # the malformed row under attack
        ]
    )
    assert coerced_id not in ids, (
        f"malformed grant subject {subject_value!r} was int()-coerced into "
        f"authorizing user {coerced_id}"
    )
    # Non-vacuous: the legitimate grant and the owner must still be present.
    assert _VALID_GRANTEE in ids
    assert _OWNER_ID in ids


def test_genuine_integer_grant_subject_still_authorizes():
    ids = _authorized_user_ids([{"subject_id": 305}])
    assert 305 in ids
    assert _OWNER_ID in ids
