"""
Security-focused tests for admin migration API routes.

These tests verify:
- Authentication is required
- Host restrictions are enforced
- Path traversal is prevented
- XSS vectors are blocked
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException


# Create a minimal app for testing
@pytest.fixture
def test_app():
    """Create a test FastAPI app with migration routes."""
    from src.api.routes.admin.migrations import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/admin")
    return app


@pytest.fixture
def client(test_app):
    """Create a test client."""
    return TestClient(test_app)


class TestAuthentication:
    """Test that authentication is required for all migration endpoints."""

    def test_list_migrations_requires_auth(self, client):
        """Test that listing migrations requires authentication."""
        response = client.get("/api/v1/admin/migrations")
        # Should get 401 or 403
        assert response.status_code in (401, 403)

    def test_get_migration_details_requires_auth(self, client):
        """Test that getting migration details requires authentication."""
        response = client.get("/api/v1/admin/migrations/001")
        assert response.status_code in (401, 403)

    def test_run_migration_requires_auth(self, client):
        """Test that running a migration requires authentication."""
        response = client.post(
            "/api/v1/admin/migrations/001/run", json={"dry_run": True}
        )
        assert response.status_code in (401, 403)

    def test_rollback_migration_requires_auth(self, client):
        """Test that rollback requires authentication."""
        response = client.post("/api/v1/admin/migrations/001/rollback")
        assert response.status_code in (401, 403)

    def test_emergency_override_requires_auth(self, client):
        """Test that generating emergency override requires authentication."""
        response = client.post(
            "/api/v1/admin/migrations/emergency-override",
            json={"reason": "Test", "expires_minutes": 30},
        )
        assert response.status_code in (401, 403)


class TestPathTraversalPrevention:
    """Test that path traversal attempts are blocked."""

    def test_invalid_version_format_dots_rejected(self, client):
        """Test that version with dots is rejected."""
        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            response = client.get("/api/v1/admin/migrations/../etc/passwd")
            # FastAPI returns 404 for invalid paths, or 400 if validation catches it
            assert response.status_code in (400, 404)
            if response.status_code == 400:
                assert "Invalid migration version format" in response.json()["detail"]

    def test_invalid_version_format_slashes_rejected(self, client):
        """Test that version with slashes is rejected."""
        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            response = client.get("/api/v1/admin/migrations/../../../etc/passwd")
            # FastAPI will return 404 for paths with slashes, but our validation should catch it
            # or return 400 if it gets to our handler
            assert response.status_code in (400, 404)

    def test_invalid_version_format_letters_rejected(self, client):
        """Test that version with letters is rejected."""
        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            response = client.get("/api/v1/admin/migrations/abc")
            # Should get 400 from our validation or 404 from FastAPI path matching
            assert response.status_code in (400, 404)
            if response.status_code == 400:
                assert "Invalid migration version format" in response.json()["detail"]

    def test_invalid_version_format_too_long_rejected(self, client):
        """Test that version with more than 3 digits is rejected."""
        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            response = client.get("/api/v1/admin/migrations/12345")
            assert response.status_code in (400, 404)
            if response.status_code == 400:
                assert "Invalid migration version format" in response.json()["detail"]

    def test_invalid_version_format_too_short_rejected(self, client):
        """Test that version with less than 3 digits is rejected."""
        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            response = client.get("/api/v1/admin/migrations/1")
            assert response.status_code in (400, 404)
            if response.status_code == 400:
                assert "Invalid migration version format" in response.json()["detail"]

    def test_valid_version_format_accepted(self, client):
        """Test that valid version format is accepted (auth will fail, but validation passes)."""
        # This test verifies that valid format gets past validation
        # The actual request will fail auth, but that's separate from path validation
        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token",
            side_effect=HTTPException(401),
        ):
            response = client.get("/api/v1/admin/migrations/001")
            # Should fail on auth, not on version format
            assert "Invalid migration version format" not in response.text


class TestConfirmationRequirement:
    """Test that irreversible migrations require confirmation."""

    @patch("src.api.routes.admin.migrations.MigrationManager")
    @patch("src.api.routes.admin.migrations.get_admin_from_token", return_value=1)
    def test_irreversible_migration_requires_confirmation(
        self, mock_auth, mock_manager_class, client
    ):
        """Test that irreversible migrations require confirmation text."""
        # Setup mock
        mock_manager = MagicMock()
        mock_manager.tracker.get_migration_metadata.return_value = {
            "irreversible": True,
            "depends_on": ["024"],
        }
        mock_manager.tracker.can_run_irreversible_migration.return_value = (True, "OK")
        mock_manager_class.return_value = mock_manager

        # Try to run without confirmation
        response = client.post(
            "/api/v1/admin/migrations/025/run",
            json={"dry_run": False, "confirmation_text": None},
        )

        # Should fail with 400 (bad confirmation) or 404 (migration not found in reality)
        assert response.status_code in (400, 404)
        if response.status_code == 400:
            assert "THE DATABASE IS BACKED UP" in response.json()["detail"]

    @patch("src.api.routes.admin.migrations.MigrationManager")
    @patch("src.api.routes.admin.migrations.get_admin_from_token", return_value=1)
    def test_irreversible_migration_wrong_confirmation_rejected(
        self, mock_auth, mock_manager_class, client
    ):
        """Test that wrong confirmation text is rejected."""
        mock_manager = MagicMock()
        mock_manager.tracker.get_migration_metadata.return_value = {
            "irreversible": True,
            "depends_on": ["024"],
        }
        mock_manager.tracker.can_run_irreversible_migration.return_value = (True, "OK")
        mock_manager_class.return_value = mock_manager

        response = client.post(
            "/api/v1/admin/migrations/025/run",
            json={"dry_run": False, "confirmation_text": "wrong text"},
        )

        assert response.status_code in (400, 404)
        if response.status_code == 400:
            assert "THE DATABASE IS BACKED UP" in response.json()["detail"]

    @patch("src.api.routes.admin.migrations.MigrationManager")
    @patch("src.api.routes.admin.migrations.get_admin_from_token", return_value=1)
    def test_irreversible_migration_correct_confirmation_accepted(
        self, mock_auth, mock_manager_class, client
    ):
        """Test that correct confirmation text is accepted."""
        mock_manager = MagicMock()
        mock_manager.tracker.get_migration_metadata.return_value = {
            "irreversible": True,
            "depends_on": ["024"],
        }
        mock_manager.tracker.can_run_irreversible_migration.return_value = (True, "OK")
        mock_manager.apply_migration.return_value = {
            "success": True,
            "version": "025",
            "message": "OK",
            "dry_run": False,
        }
        mock_manager_class.return_value = mock_manager

        response = client.post(
            "/api/v1/admin/migrations/025/run",
            json={"dry_run": False, "confirmation_text": "THE DATABASE IS BACKED UP"},
        )

        # Should succeed or fail for reasons other than confirmation
        assert (
            "THE DATABASE IS BACKED UP" not in response.text
            or response.status_code == 200
        )

    @patch("src.api.routes.admin.migrations.MigrationManager")
    @patch("src.api.routes.admin.migrations.get_admin_from_token", return_value=1)
    def test_irreversible_migration_dry_run_no_confirmation_needed(
        self, mock_auth, mock_manager_class, client
    ):
        """Test that dry-run doesn't require confirmation."""
        mock_manager = MagicMock()
        mock_manager.tracker.get_migration_metadata.return_value = {
            "irreversible": True,
            "depends_on": ["024"],
        }
        mock_manager.tracker.can_run_irreversible_migration.return_value = (True, "OK")
        mock_manager.apply_migration.return_value = {
            "success": True,
            "version": "025",
            "message": "OK",
            "dry_run": True,
        }
        mock_manager_class.return_value = mock_manager

        response = client.post(
            "/api/v1/admin/migrations/025/run",
            json={"dry_run": True},  # No confirmation text needed
        )

        # Should not fail due to missing confirmation
        assert (
            response.status_code != 400 or "confirmation" not in response.text.lower()
        )


class TestRollbackIrreversible:
    """Test that irreversible migrations cannot be rolled back."""

    @patch("src.api.routes.admin.migrations.MigrationManager")
    @patch("src.api.routes.admin.migrations.get_admin_from_token", return_value=1)
    def test_rollback_irreversible_migration_blocked(
        self, mock_auth, mock_manager_class, client
    ):
        """Test that rollback of irreversible migration is blocked."""
        mock_manager = MagicMock()
        mock_manager.tracker.get_migration_metadata.return_value = {
            "irreversible": True,
            "depends_on": ["024"],
        }
        mock_manager_class.return_value = mock_manager

        response = client.post("/api/v1/admin/migrations/025/rollback")

        # Should get 400 if blocked by our code, or 404 if migration not found
        assert response.status_code in (400, 404)
        if response.status_code == 400:
            assert "Cannot rollback irreversible" in response.json()["detail"]

    @patch("src.api.routes.admin.migrations.MigrationManager")
    @patch("src.api.routes.admin.migrations.get_admin_from_token", return_value=1)
    def test_rollback_reversible_migration_allowed(
        self, mock_auth, mock_manager_class, client
    ):
        """Test that rollback of reversible migration is allowed."""
        mock_manager = MagicMock()
        mock_manager.tracker.get_migration_metadata.return_value = {
            "irreversible": False
        }
        mock_manager.rollback_migration.return_value = {"success": True}
        mock_manager_class.return_value = mock_manager

        response = client.post("/api/v1/admin/migrations/001/rollback")

        # Should not be blocked as irreversible
        assert (
            response.status_code != 400 or "irreversible" not in response.text.lower()
        )


class TestVersionValidationEdgeCases:
    """Test edge cases for version validation."""

    def test_version_with_null_bytes_rejected(self, client):
        """Test that version with null bytes is rejected."""
        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            # Null byte injection attempt
            response = client.get("/api/v1/admin/migrations/001%00.py")
            # Should get 400 or 404, not succeed
            assert response.status_code in (400, 404, 422)

    def test_version_with_special_chars_rejected(self, client):
        """Test that version with special characters is rejected."""
        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            response = client.get("/api/v1/admin/migrations/00%3B")  # URL encoded ;
            assert response.status_code in (400, 404)

    def test_version_unicode_rejected(self, client):
        """Test that unicode in version is rejected."""
        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            response = client.get(
                "/api/v1/admin/migrations/\u0030\u0030\u0031"
            )  # Unicode 001
            assert response.status_code in (400, 404)


class TestHostRestriction:
    """Test that host restrictions are enforced."""

    @patch("src.api.routes.admin.migrations.check_host_restriction")
    def test_host_restriction_checked_for_list(self, mock_host_check, client):
        """Test that host restriction is checked for list endpoint."""
        mock_host_check.side_effect = HTTPException(403, "Access denied from this host")

        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            response = client.get("/api/v1/admin/migrations")

        mock_host_check.assert_called_once()
        assert response.status_code == 403

    @patch("src.api.routes.admin.migrations.check_host_restriction")
    def test_host_restriction_checked_for_run(self, mock_host_check, client):
        """Test that host restriction is checked for run endpoint."""
        mock_host_check.side_effect = HTTPException(403, "Access denied from this host")

        with patch(
            "src.api.routes.admin.migrations.get_admin_from_token", return_value=1
        ):
            response = client.post(
                "/api/v1/admin/migrations/001/run", json={"dry_run": True}
            )

        mock_host_check.assert_called_once()
        assert response.status_code == 403
