"""Regression tests for batched message lookup in AutoMod bulk scanning.

`scan_messages_bulk` used to issue one ``SELECT ... WHERE id = ?`` per message
(an N+1 read). It now resolves messages through chunked ``IN`` queries. These
tests pin three properties:

* result parity with the old per-id loop (ordering, duplicates, skips),
* query-count reduction from one-per-message to one-per-500-id chunk,
* a hard bound of 500 binds per query, with the ``deleted = 0`` predicate kept.
"""

import math

from src.core.automod.manager.evaluation import (
    EvaluationMixin,
    _BULK_LOOKUP_CHUNK,
)
from src.core.automod.models import BulkScanResult, CheckResult

SERVER_ID = 10
CHANNEL_ID = 20


class _FakeDB:
    """Minimal DB double recording queries and serving message rows by id."""

    def __init__(self, rows):
        self._rows = rows
        self.fetch_one_calls = []
        self.fetch_all_calls = []

    def fetch_one(self, sql, params=()):
        self.fetch_one_calls.append((sql, tuple(params)))
        # Legacy semantics used by the parity reference below.
        if "deleted = 0" not in sql:
            raise AssertionError("per-id lookup lost the deleted = 0 predicate")
        row = self._rows.get(params[0])
        if row is not None and not row["deleted"]:
            return row
        return None

    def fetch_all(self, sql, params=()):
        self.fetch_all_calls.append((sql, tuple(params)))
        if "deleted = 0" not in sql or " IN (" not in sql:
            raise AssertionError("batched lookup lost its scope/predicate")
        return [
            self._rows[mid]
            for mid in params
            if mid in self._rows and not self._rows[mid]["deleted"]
        ]


class _Harness(EvaluationMixin):
    """Bare evaluation mixin with a controlled check_message verdict map."""

    def __init__(self, db, verdicts):
        self._db = db
        self._verdicts = verdicts

    def _get_timestamp(self):
        return 1_000

    def check_message(self, **kwargs):
        passed = self._verdicts.get(kwargs["message_id"], True)
        return CheckResult(passed=passed, violations=[], actions_to_take=[])


def _legacy_scan(harness, message_ids):
    """Reference implementation: one fetch per id, in caller order."""
    messages_flagged = []
    user_violations = {}
    for message_id in message_ids:
        msg = harness._db.fetch_one(
            "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
        )
        if not msg:
            continue
        content = msg.get("content", "")
        user_id = msg["author_id"]
        result = harness.check_message(
            server_id=SERVER_ID,
            channel_id=CHANNEL_ID,
            user_id=user_id,
            content=content,
            message_id=message_id,
            context=None,
        )
        if not result.passed:
            messages_flagged.append(message_id)
            user_violations[user_id] = user_violations.get(user_id, 0) + 1
    return BulkScanResult(
        total_scanned=len(message_ids),
        violations_found=len(messages_flagged),
        messages_flagged=messages_flagged,
        user_violations=user_violations,
        scan_duration_ms=0,
    )


def _dataset():
    rows = {}
    for mid in range(1, 1_101):
        rows[mid] = {
            "id": mid,
            "content": "spam" if mid % 7 == 0 else "clean",
            "author_id": 100 + (mid % 5),
            "deleted": mid % 11 == 0,
        }
    # passed=False only for spam content so those ids get flagged.
    verdicts = {mid: row["content"] != "spam" for mid, row in rows.items()}
    # Duplicate a flagged id and add a missing id to exercise parity.
    ids = list(range(1, 1_101)) + [7, 7, 999_999]
    return rows, verdicts, ids


def test_scan_messages_bulk_result_parity_with_per_id_loop():
    rows, verdicts, ids = _dataset()

    expected = _legacy_scan(_Harness(_FakeDB(rows), verdicts), ids)

    db = _FakeDB(rows)
    result = _Harness(db, verdicts).scan_messages_bulk(
        server_id=SERVER_ID, channel_id=CHANNEL_ID, message_ids=ids
    )

    assert result.total_scanned == expected.total_scanned
    assert result.violations_found == expected.violations_found
    assert result.messages_flagged == expected.messages_flagged
    assert result.user_violations == expected.user_violations

    # Explicit checks: deleted and missing ids never flagged, duplicates kept.
    assert 11 not in result.messages_flagged  # deleted
    assert 999_999 not in result.messages_flagged  # missing
    assert result.messages_flagged.count(7) == 3  # 7 once in range + 2 duplicates


def test_scan_messages_bulk_query_count_and_bind_bounds():
    rows, verdicts, ids = _dataset()

    db = _FakeDB(rows)
    _Harness(db, verdicts).scan_messages_bulk(
        server_id=SERVER_ID, channel_id=CHANNEL_ID, message_ids=ids
    )

    # No per-id reads remain; one bounded query per chunk instead of one per id.
    assert db.fetch_one_calls == []
    expected_queries = math.ceil(len(ids) / _BULK_LOOKUP_CHUNK)
    assert len(db.fetch_all_calls) == expected_queries
    assert expected_queries < len(ids)

    for _sql, params in db.fetch_all_calls:
        assert 1 <= len(params) <= _BULK_LOOKUP_CHUNK


def test_scan_messages_bulk_empty_input_does_no_queries():
    db = _FakeDB({})
    result = _Harness(db, {}).scan_messages_bulk(
        server_id=SERVER_ID, channel_id=CHANNEL_ID, message_ids=[]
    )

    assert result.total_scanned == 0
    assert result.messages_flagged == []
    assert db.fetch_all_calls == []
    assert db.fetch_one_calls == []
