"""Regression tests: admin moderation listings must clamp ``limit`` from below.

The ``get_hash_reports`` / ``get_blocked_hashes`` / ``get_blocked_users``
helpers in ``src.core.admin.moderation`` forwarded the caller's ``limit``
straight into SQL ``LIMIT ?``. SQLite reads ``LIMIT -1`` as *unlimited*, so
``GET /admin/moderation/hash-reports?limit=-1`` (and the blocked-hash /
blocked-user listings) returned *every* row instead of an empty page. The
helpers now clamp ``limit`` to ``[0, MAX_PAGE_SIZE]`` and ``offset`` to
``>= 0``.
"""

from src.core.admin import moderation
from src.core.media.deduplication.constants import create_tables


def _seed(db, reporter_id):
    """Create the moderation tables and two rows in each listing."""
    create_tables(db)
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


class TestAdminModerationNegativeLimitClamp:
    def test_hash_reports_negative_limit_is_empty(self, db, registered_user):
        user, _auth, _username = registered_user
        _seed(db, user.id)

        assert moderation.get_hash_reports(db, limit=-1) == []
        assert moderation.get_hash_reports(db, limit=0) == []
        # A positive limit still returns the seeded rows.
        assert len(moderation.get_hash_reports(db, limit=10)) == 2

    def test_blocked_hashes_negative_limit_is_empty(self, db, registered_user):
        user, _auth, _username = registered_user
        _seed(db, user.id)

        assert moderation.get_blocked_hashes(db, limit=-1) == []
        assert moderation.get_blocked_hashes(db, limit=0) == []
        assert len(moderation.get_blocked_hashes(db, limit=10)) == 2

    def test_blocked_users_negative_limit_is_empty(self, db, registered_user):
        user, _auth, _username = registered_user
        _seed(db, user.id)

        assert moderation.get_blocked_users(db, limit=-1) == []
        assert moderation.get_blocked_users(db, limit=0) == []
        assert len(moderation.get_blocked_users(db, limit=10)) == 1

    def test_limit_is_capped_at_max_page_size(self, db, registered_user):
        user, _auth, _username = registered_user
        _seed(db, user.id)

        # The page bound is clamped on both sides, so even a huge limit can
        # never materialize an unbounded result set.
        assert len(moderation.get_hash_reports(db, limit=10**6)) == 2
