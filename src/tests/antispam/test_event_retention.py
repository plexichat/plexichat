"""Regression tests: dm_spam_events retention prune.

M6 (audit): every blocked attempt INSERTed a dm_spam_events row with no
cap and no cleanup, so the table grew unbounded on busy instances. The
fix adds ``DMSpamDetector.prune_old_events()``, wired into the periodic
maintenance sweep, deleting rows older than the retention horizon.
"""

import pytest

from src.core.antispam.detector import DMSpamDetector


@pytest.fixture
def detector(db):
    return DMSpamDetector(db)


def _insert_event(detector, sender_id, recipient_id, created_at, event_type="rate"):
    detector._db.execute(
        """INSERT INTO dm_spam_events
           (id, sender_id, recipient_id, event_type, content_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            detector._generate_id(),
            sender_id,
            recipient_id,
            event_type,
            None,
            created_at,
        ),
    )


class TestEventRetention:
    def test_prune_removes_only_rows_older_than_retention(self, detector):
        now = detector._get_timestamp()
        old_ts = now - (200 * 3600 * 1000)  # ~8.3 days old
        recent_ts = now - (1000)  # 1 second old

        _insert_event(detector, 11, 22, old_ts)
        _insert_event(detector, 33, 22, recent_ts)

        pruned = detector.prune_old_events(retention_hours=168)

        assert pruned == 1
        remaining = detector._db.fetch_all(
            "SELECT created_at FROM dm_spam_events WHERE recipient_id = ?",
            (22,),
        )
        stamps = [r["created_at"] if isinstance(r, dict) else r[0] for r in remaining]
        assert recent_ts in stamps
        assert old_ts not in stamps

    def test_prune_zero_retention_disabled(self, detector):
        now = detector._get_timestamp()
        _insert_event(detector, 44, 55, now - (10 * 3600 * 1000))
        # retention_hours <= 0 disables the sweep entirely (fail-safe).
        assert detector.prune_old_events(retention_hours=0) == 0

    def test_prune_is_idempotent(self, detector):
        now = detector._get_timestamp()
        _insert_event(detector, 66, 77, now - (400 * 3600 * 1000))
        first = detector.prune_old_events(retention_hours=168)
        second = detector.prune_old_events(retention_hours=168)
        assert first == 1
        assert second == 0
