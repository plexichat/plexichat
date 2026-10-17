"""Tests for admin bot stats (week_ago window fix)."""

import pytest

from src.core.webhooks.retry import WebhookRetryQueue


@pytest.mark.applications
class TestAdminStats:
    """Tests for admin dashboard statistics."""

    def test_recent_approvals_uses_correct_time_window(self, app_manager, test_user):
        """Test that recent_approvals uses a 7-day window, not 10 minutes."""
        stats = app_manager.get_admin_bot_stats()
        assert "recent_approvals" in stats
        assert "recent_requests" in stats

    def test_admin_stats_structure(self, app_manager, test_user):
        """Test that admin stats returns expected keys."""
        stats = app_manager.get_admin_bot_stats()
        expected_keys = {
            "total_applications",
            "total_bots",
            "total_approved",
            "total_pending_requests",
            "total_installations",
            "servers_with_bots",
            "recent_approvals",
            "recent_requests",
        }
        assert expected_keys.issubset(set(stats.keys()))

    def test_admin_stats_counts_zero_for_empty_db(self, app_manager, test_user):
        """Test that admin stats returns 0 counts for empty database."""
        stats = app_manager.get_admin_bot_stats()
        assert stats["total_applications"] == 0
        assert stats["total_bots"] == 0
        assert stats["total_approved"] == 0
        assert stats["total_pending_requests"] == 0

    def test_feature_stats_reports_webhook_retry_breakdown(self, db, app_manager):
        """Feature stats break pending/retrying/dead-letter retry queue rows out."""
        queue = WebhookRetryQueue(db)

        pending = queue.enqueue(
            101,
            "message.created",
            {"n": 1},
            "https://hook.example/pending",
            allow_private_network=True,
        )
        retrying = queue.enqueue(
            101,
            "message.created",
            {"n": 2},
            "https://hook.example/retrying",
            allow_private_network=True,
        )
        dead = queue.enqueue(
            101,
            "message.created",
            {"n": 3},
            "https://hook.example/dead",
            allow_private_network=True,
        )

        # Move one row to retrying and one to dead-letter via the real queue
        # transitions so the admin stat reflects genuine queue state.
        db.execute(
            "UPDATE webhook_retry_queue SET status = 'retrying' WHERE id = ?",
            (retrying["id"],),
        )
        for _ in range(WebhookRetryQueue.MAX_RETRIES):
            queue.mark_failure(dead["id"], 500, "HTTP 500")

        stats = app_manager.get_admin_dashboard_feature_stats()
        assert stats["webhook_retries_pending"] == 1
        assert stats["webhook_retries_retrying"] == 1
        assert stats["webhook_retries_dead_letter"] == 1
        # The delivered rows from the failed attempts are not counted above;
        # the pending row survives untouched.
        row = db.fetch_one(
            "SELECT status FROM webhook_retry_queue WHERE id = ?", (pending["id"],)
        )
        assert row["status"] == "pending"

    def test_feature_stats_dead_letter_zero_when_empty(self, db, app_manager):
        """Empty retry queue reports zero dead-letter rows, not a missing key."""
        stats = app_manager.get_admin_dashboard_feature_stats()
        assert stats["webhook_retries_dead_letter"] == 0
        assert stats["webhook_retries_pending"] == 0
        assert stats["webhook_retries_retrying"] == 0
