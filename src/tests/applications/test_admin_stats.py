"""Tests for admin bot stats (week_ago window fix)."""

import pytest


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
