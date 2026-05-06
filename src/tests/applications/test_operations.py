"""Tests for application CRUD operations."""

import pytest

from src.core.applications.exceptions import (
    ApplicationNotFoundError,
    ApplicationAccessDeniedError,
    InvalidApplicationNameError,
)


@pytest.mark.applications
class TestOperations:
    """Tests for application create, get, update, delete operations."""

    def test_create_application(self, app_manager, test_user):
        """Test creating an application."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Test App",
            description="A test application",
        )
        assert app.name == "Test App"
        assert app.owner_id == test_user.id
        assert app.description == "A test application"
        assert app.bot_public is True
        assert app.client_secret is not None

    def test_get_application(self, app_manager, test_user):
        """Test retrieving an application by ID."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Get App",
        )
        retrieved = app_manager.get_application(app.id)
        assert retrieved is not None
        assert retrieved.id == app.id
        assert retrieved.name == "Get App"

    def test_get_nonexistent_application(self, app_manager):
        """Test getting a nonexistent application returns None."""
        result = app_manager.get_application(9999999)
        assert result is None

    def test_update_application(self, app_manager, test_user):
        """Test updating an application."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Old Name",
        )
        updated = app_manager.update_application(
            user_id=test_user.id,
            application_id=app.id,
            name="New Name",
            description="Updated description",
        )
        assert updated.name == "New Name"
        assert updated.description == "Updated description"

    def test_update_application_not_owner(self, app_manager, two_users):
        """Test that non-owner cannot update application."""
        owner, other = two_users
        app = app_manager.create_application(
            owner_id=owner.id,
            name="Owner App",
        )
        with pytest.raises(ApplicationAccessDeniedError):
            app_manager.update_application(
                user_id=other.id,
                application_id=app.id,
                name="Hacked Name",
            )

    def test_update_nonexistent_application(self, app_manager, test_user):
        """Test updating nonexistent application raises error."""
        with pytest.raises(ApplicationNotFoundError):
            app_manager.update_application(
                user_id=test_user.id,
                application_id=9999999,
                name="No App",
            )

    def test_delete_application(self, app_manager, test_user):
        """Test deleting an application."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="To Delete",
        )
        result = app_manager.delete_application(test_user.id, app.id)
        assert result is True
        assert app_manager.get_application(app.id) is None

    def test_delete_application_not_owner(self, app_manager, two_users):
        """Test that non-owner cannot delete application."""
        owner, other = two_users
        app = app_manager.create_application(
            owner_id=owner.id,
            name="Owner App",
        )
        with pytest.raises(ApplicationAccessDeniedError):
            app_manager.delete_application(other.id, app.id)

    def test_delete_nonexistent_application(self, app_manager, test_user):
        """Test deleting nonexistent application raises error."""
        with pytest.raises(ApplicationNotFoundError):
            app_manager.delete_application(test_user.id, 9999999)

    def test_get_user_applications(self, app_manager, test_user):
        """Test getting all applications for a user."""
        app_manager.create_application(owner_id=test_user.id, name="App 1")
        app_manager.create_application(owner_id=test_user.id, name="App 2")
        apps = app_manager.get_user_applications(test_user.id)
        assert len(apps) >= 2
        names = {a.name for a in apps}
        assert "App 1" in names
        assert "App 2" in names

    def test_invalid_name_empty(self, app_manager, test_user):
        """Test that empty application name is rejected."""
        with pytest.raises(InvalidApplicationNameError):
            app_manager.create_application(owner_id=test_user.id, name="")

    def test_invalid_name_too_short(self, app_manager, test_user):
        """Test that single-character name is rejected."""
        with pytest.raises(InvalidApplicationNameError):
            app_manager.create_application(owner_id=test_user.id, name="A")

    def test_invalid_name_too_long(self, app_manager, test_user):
        """Test that overly long name is rejected."""
        with pytest.raises(InvalidApplicationNameError):
            app_manager.create_application(owner_id=test_user.id, name="X" * 101)

    def test_regenerate_client_secret(self, app_manager, test_user):
        """Test regenerating client secret."""
        app = app_manager.create_application(owner_id=test_user.id, name="Secret App")
        old_secret = app.client_secret
        new_secret = app_manager.regenerate_client_secret(test_user.id, app.id)
        assert new_secret != old_secret

    def test_create_application_with_redirect_uris(self, app_manager, test_user):
        """Test creating an application with redirect URIs."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="OAuth App",
            redirect_uris=["https://example.com/callback"],
        )
        assert app.redirect_uris == ["https://example.com/callback"]
