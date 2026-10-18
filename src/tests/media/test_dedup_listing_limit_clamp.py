"""Regression tests: deduplication listing ``limit`` clamped from below.

``DeduplicationManager.get_reports`` / ``get_blocked_hashes`` /
``get_blocked_users`` forwarded ``limit`` straight into SQL ``LIMIT ?``.
SQLite reads ``LIMIT -1`` as *unlimited*, so a negative ``limit`` dumped the
whole table instead of an empty page. The manager now clamps ``limit`` (and
``offset``) from below.
"""

from src.core.media.deduplication import DeduplicationManager
from src.core.media.deduplication.constants import create_tables


def _manager(db):
    create_tables(db)
    return DeduplicationManager(db)


def _seed(db, reporter_id):
    for report_id, marker in ((1, "a"), (2, "b")):
        db.execute(
            "INSERT INTO media_hash_reports "
            "(id, hash_value, reporter_id, reason, status, reported_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (report_id, marker * 64, reporter_id, "test report", 1000 + report_id),
        )
    for marker, blocked_at in (("c", 1000), ("d", 1001)):
        db.execute(
            "INSERT INTO media_blocked_hashes "
            "(hash_value, hash_type, phash_threshold, reason, blocked_at, "
            " blocked_by, auto_blocked) "
            "VALUES (?, 'sha256', 10, ?, ?, NULL, 0)",
            (marker * 64, "blocked", blocked_at),
        )
    db.execute(
        "INSERT INTO media_blocked_users "
        "(user_id, reason, blocked_at, blocked_by, expires_at) "
        "VALUES (?, ?, ?, NULL, NULL)",
        (reporter_id, "blocked", 1000),
    )


class TestDedupListingNegativeLimitClamp:
    def test_get_reports_negative_limit_is_empty(self, db, registered_user):
        user, _auth, _username = registered_user
        manager = _manager(db)
        _seed(db, user.id)

        assert manager.get_reports(limit=-1) == []
        assert manager.get_reports(limit=0) == []
        assert len(manager.get_reports(limit=10)) == 2

    def test_get_blocked_hashes_negative_limit_is_empty(self, db, registered_user):
        user, _auth, _username = registered_user
        manager = _manager(db)
        _seed(db, user.id)

        assert manager.get_blocked_hashes(limit=-1) == []
        assert manager.get_blocked_hashes(limit=0) == []
        assert len(manager.get_blocked_hashes(limit=10)) == 2

    def test_get_blocked_users_negative_limit_is_empty(self, db, registered_user):
        user, _auth, _username = registered_user
        manager = _manager(db)
        _seed(db, user.id)

        assert manager.get_blocked_users(limit=-1) == []
        assert manager.get_blocked_users(limit=0) == []
        assert len(manager.get_blocked_users(limit=10)) == 1
