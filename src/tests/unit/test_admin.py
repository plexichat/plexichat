"""
Unit tests for the admin module.
"""

import pytest
from unittest.mock import MagicMock


class TestAdminModule:
    """Tests for admin module functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.execute = MagicMock()
        db.fetch_all = MagicMock(return_value=[])
        db.fetch_one = MagicMock(return_value=None)
        db.convert_schema = MagicMock(side_effect=lambda x: x)
        return db

    @pytest.fixture
    def admin_module(self, mock_db):
        """Setup admin module with mock db."""
        from src.core import admin

        admin.setup(mock_db)
        return admin

    def test_setup_creates_tables(self, mock_db):
        """Test that setup creates required tables."""
        from src.core import admin

        admin.setup(mock_db)

        assert mock_db.execute.called

    def test_is_setup_returns_true(self, admin_module):
        """Test is_setup returns True after initialization."""
        assert admin_module.is_setup() is True

    def test_is_admin_returns_false_for_non_admin(self, admin_module, mock_db):
        """Test is_admin returns False for non-admin user."""
        mock_db.fetch_one.return_value = {"permissions": "{}"}

        assert admin_module.is_admin(123) is False

    def test_is_admin_returns_true_for_admin(self, admin_module, mock_db):
        """Test is_admin returns True for admin user."""
        mock_db.fetch_one.return_value = {"permissions": '{"*": true}'}

        assert admin_module.is_admin(123) is True

    def test_set_admin_updates_flag(self, admin_module, mock_db):
        """Test set_admin updates the is_admin flag."""
        mock_db.fetch_one.return_value = {"permissions": "{}"}
        result = admin_module.set_admin(123, True)

        assert result is True
        # Check that it tried to update permissions
        args, _ = mock_db.execute.call_args
        assert "UPDATE auth_users SET permissions =" in args[0]
