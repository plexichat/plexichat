"""Conversation-visibility allow set must not grant on a malformed member row.

``AccessControlMixin._can_access_conversations_bulk`` answers, for a batch of
conversation ids, which ones the searcher may actually see. Its allow set is
the live ``msg_participants.conversation_id`` projection for that user, and it
used to coerce every projected value with ``int()`` before the membership test.

A malformed-but-coercible row -- ``"05"``, a padded string, a float ``5.9`` or
a bool ``True`` -- silently becomes a real conversation id and is admitted to
the allow set, so a message in that conversation becomes visible even though
the row does not prove the user's participation. Only genuine integer ids from
live participant rows may satisfy the visibility gate.
"""

import pytest

from src.core.search.manager.access import AccessControlMixin

pytestmark = pytest.mark.unit


class _DB:
    """Minimal fetch_all stub returning the crafted authoritative rows."""

    def __init__(self, rows):
        self.rows = rows

    def fetch_all(self, sql, params):
        return list(self.rows)


class _Access(AccessControlMixin):
    def __init__(self, rows):
        self._db = _DB(rows)


def _verdict(rows, requested):
    return _Access(rows)._can_access_conversations_bulk(
        user_id=99, conversation_ids=requested
    )


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
def test_malformed_participant_row_does_not_grant_visibility(
    malformed_cid, coerced_cid
):
    verdict = _verdict([{"conversation_id": malformed_cid}], [coerced_cid])
    assert verdict[coerced_cid] is False, (
        f"malformed conversation_id {malformed_cid!r} coerced into a visibility "
        f"grant for conversation {coerced_cid}"
    )


def test_genuine_integer_row_still_grants():
    """The gate still admits a real participant (test is non-vacuous)."""
    assert _verdict([{"conversation_id": 7}], [7])[7] is True


def test_non_participant_and_empty_rows_fail_closed():
    assert _verdict([{"conversation_id": 7}], [9])[9] is False
    assert _verdict([], [7])[7] is False
