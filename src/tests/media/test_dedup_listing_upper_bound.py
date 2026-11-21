"""Regression tests: deduplication listing ``limit`` must be clamped from above.

The peer that added ``test_dedup_listing_limit_clamp.py`` only clamped these
listing helpers from *below* (``max(0, limit)``). SQLite reads ``LIMIT ?`` with
a very large positive value as "up to N", so an oversized ``limit`` still
materialized the entire moderation table, and a non-int ``None``/``"abc"``
raised instead of failing closed. This module pins the upper bound and the
fail-closed coercion at the manager boundary.

Expected max (200) mirrors ``admin.moderation.MAX_PAGE_SIZE``; the manager
method is the shared boundary, so these constraints apply to direct callers
too, not just HTTP routes.
"""

from src.core.media.deduplication import DeduplicationManager
from src.core.media.deduplication.constants import create_tables

EXPECTED_MAX = 200
# More rows than any sane page, so an unbounded LIMIT is observable.
TOTAL = EXPECTED_MAX + 50


def _manager(db):
    create_tables(db)
    return DeduplicationManager(db)


def _seed_many(db, reporter_id):
    for i in range(TOTAL):
        hash_value = f"{i:064d}"
        db.execute(
            "INSERT INTO media_hash_reports "
            "(id, hash_value, reporter_id, reason, status, reported_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (i + 1, hash_value, reporter_id, "test report", 1000 + i),
        )
        db.execute(
            "INSERT INTO media_blocked_hashes "
            "(hash_value, hash_type, phash_threshold, reason, blocked_at, "
            " blocked_by, auto_blocked) "
            "VALUES (?, 'sha256', 10, ?, ?, NULL, 0)",
            (hash_value, "blocked", 1000 + i),
        )
        db.execute(
            "INSERT INTO media_blocked_users "
            "(user_id, reason, blocked_at, blocked_by, expires_at) "
            "VALUES (?, ?, ?, NULL, NULL)",
            (i + 1, "blocked", 1000 + i),
        )


class TestDedupListingUpperBound:
    def test_oversized_limit_is_clamped_to_page_max(self, db, registered_user):
        user, _auth, _username = registered_user
        manager = _manager(db)
        _seed_many(db, user.id)

        huge = 10**9
        assert len(manager.get_reports(limit=huge)) == EXPECTED_MAX
        assert len(manager.get_blocked_hashes(limit=huge)) == EXPECTED_MAX
        assert len(manager.get_blocked_users(limit=huge)) == EXPECTED_MAX

    def test_normal_limits_unchanged(self, db, registered_user):
        user, _auth, _username = registered_user
        manager = _manager(db)
        _seed_many(db, user.id)

        assert len(manager.get_reports(limit=5)) == 5
        assert len(manager.get_blocked_hashes(limit=7)) == 7
        assert len(manager.get_blocked_users(limit=9)) == 9
        # Defaults sit below the cap and must still be honored verbatim.
        assert len(manager.get_reports()) == 50
        assert len(manager.get_blocked_hashes()) == 100
        assert len(manager.get_blocked_users()) == 100

    def test_invalid_input_fails_closed_to_empty(self, db, registered_user):
        user, _auth, _username = registered_user
        manager = _manager(db)
        _seed_many(db, user.id)

        invalid = (
            None,
            -1,
            "abc",
            "1_0",
            "٤٢",
            float("inf"),
            float("nan"),
            3.5,
            True,
            10**30,
        )
        for bad in invalid:
            assert manager.get_reports(limit=bad) == [], f"reports: {bad!r}"
            assert manager.get_blocked_hashes(limit=bad) == [], f"hashes: {bad!r}"
            assert manager.get_blocked_users(limit=bad) == [], f"users: {bad!r}"
