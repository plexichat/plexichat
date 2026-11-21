"""Bulk participation allow set must not grant on a malformed participant row.

``ParticipantService.are_participants_bulk`` answers, for a batch of
conversation ids, which ones the searcher participates in. Its direct allow
set is the live ``msg_participants.conversation_id`` projection for that user,
and it used to coerce every projected value with ``int()`` before the
membership test.

A malformed-but-coercible row -- ``"05"``, a padded string, a float ``5.9`` or
a bool ``True`` -- silently becomes a real conversation id and is admitted to
the allow set, so participation (and the message access that follows from it)
is granted for a conversation the row does not prove. Only genuine integer ids
from live participant rows may satisfy the gate.
"""

import pytest

from src.core.messaging.services.participant import ParticipantService

pytestmark = pytest.mark.unit

_USER = 99


class _Repo:
    """Minimal repo: return crafted participant rows, empty fallback rows."""

    def __init__(self, rows):
        self.rows = rows

    def _build_in_clause(self, items):
        if not items:
            return "(NULL)", ()
        return "(" + ",".join("?" * len(items)) + ")", tuple(items)

    def _fetch_all(self, query, params=None):
        if "msg_participants" in query:
            return list(self.rows)
        return []


class _Service(ParticipantService):
    def __init__(self, rows):
        self._repo = _Repo(rows)
        self._cache = {}

    def _cache_get(self, key, default=None):
        return self._cache.get(key, default)

    def _cache_set(self, key, value):
        self._cache[key] = value


def _verdict(row_value, requested):
    service = _Service([{"conversation_id": row_value}])
    return service.are_participants_bulk(requested, _USER)


@pytest.mark.parametrize(
    "malformed_cid, coerced_cid",
    [
        ("05", 5),  # leading zero: int() -> 5, not the stored identity
        (" 5 ", 5),  # padded string
        (5.9, 5),  # float truncates to 5
        (True, 1),  # bool coerces to 1
        ("1", 1),  # numeric string
    ],
)
def test_malformed_participant_row_does_not_grant(malformed_cid, coerced_cid):
    verdict = _verdict(malformed_cid, [coerced_cid])
    assert verdict[coerced_cid] is False, (
        f"malformed conversation_id {malformed_cid!r} coerced into a "
        f"participation grant for conversation {coerced_cid}"
    )


def test_genuine_integer_row_still_grants():
    """The gate still admits a real participant (test is non-vacuous)."""
    assert _verdict(7, [7])[7] is True


def test_non_participant_and_empty_rows_fail_closed():
    assert _verdict(7, [9])[9] is False
    service = _Service([])
    assert service.are_participants_bulk([7], _USER)[7] is False
