"""
Tests for application CRUD operations.
"""

import pytest


@pytest.mark.applications
@pytest.mark.integration
class TestApplicationCreate:
    """Tests for application creation."""

    def test_create_application(self, modules, user_pool):
        """Test creating a basic application."""
        owner = user_pool.get_user()

        app = modules.applications.create_application(
            owner_id=owner.id,
            name="Test Bot",
            description="A test bot application",
        )

        assert app.id is not None
        assert app.owner_id == owner.id
        assert app.name == "Test Bot"
        assert app.description == "A test bot application"
        assert app.client_secret is not None
        assert app.bot_id is None

    def test_create_application_with_redirect_uris(self, modules, user_pool):
        """Test creating application with OAuth redirect URIs."""
        owner = user_pool.get_user()

        app = modules.applications.create_application(
            owner_id=owner.id,
            name="OAuth App",
            redirect_uris=[
                "https://example.com/callback",
                "https://example.com/auth",
            ],
        )

        assert len(app.redirect_uris) == 2
        assert "https://example.com/callback" in app.redirect_uris

    def test_create_application_with_all_fields(self, modules, user_pool):
        """Test creating application with all optional fields."""
        owner = user_pool.get_user()

        app = modules.applications.create_application(
            owner_id=owner.id,
            name="Full App",
            description="Complete application",
            redirect_uris=["https://example.com/callback"],
            bot_public=False,
            bot_require_code_grant=True,
            terms_of_service_url="https://example.com/tos",
            privacy_policy_url="https://example.com/privacy",
            interactions_endpoint_url="https://example.com/interactions",
        )

        assert app.bot_public is False
        assert app.bot_require_code_grant is True
        assert app.terms_of_service_url == "https://example.com/tos"
        assert app.privacy_policy_url == "https://example.com/privacy"
        assert app.interactions_endpoint_url == "https://example.com/interactions"


@pytest.mark.applications
@pytest.mark.integration
class TestApplicationRead:
    """Tests for reading applications."""

    def test_get_application(self, modules, test_application):
        """Test getting an application by ID."""
        app, owner = test_application

        fetched = modules.applications.get_application(app.id)

        assert fetched is not None
        assert fetched.id == app.id
        assert fetched.name == app.name

    def test_get_nonexistent_application(self, modules):
        """Test getting a non-existent application."""
        result = modules.applications.get_application(999999999)
        assert result is None

    def test_get_user_applications(self, modules, user_pool, app_factory):
        """Test getting all applications for a user."""
        owner = user_pool.get_user()

        app_factory.create(owner=owner, name="App 1")
        app_factory.create(owner=owner, name="App 2")
        app_factory.create(owner=owner, name="App 3")

        apps = modules.applications.get_user_applications(owner.id)

        assert len(apps) >= 3
        names = [a.name for a in apps]
        assert "App 1" in names
        assert "App 2" in names
        assert "App 3" in names


@pytest.mark.applications
@pytest.mark.integration
class TestApplicationUpdate:
    """Tests for updating applications."""

    def test_update_application_name(self, modules, test_application):
        """Test updating application name."""
        app, owner = test_application

        updated = modules.applications.update_application(
            user_id=owner.id,
            application_id=app.id,
            name="Updated Name",
        )

        assert updated.name == "Updated Name"

    def test_update_application_description(self, modules, test_application):
        """Test updating application description."""
        app, owner = test_application

        updated = modules.applications.update_application(
            user_id=owner.id,
            application_id=app.id,
            description="New description",
        )

        assert updated.description == "New description"

    def test_update_application_redirect_uris(self, modules, test_application):
        """Test updating redirect URIs."""
        app, owner = test_application

        updated = modules.applications.update_application(
            user_id=owner.id,
            application_id=app.id,
            redirect_uris=["https://new.example.com/callback"],
        )

        assert "https://new.example.com/callback" in updated.redirect_uris

    def test_update_application_not_owner(self, modules, test_application, user_pool):
        """Test that non-owner cannot update application."""
        app, owner = test_application
        other_user = user_pool.get_user()

        with pytest.raises(modules.applications.ApplicationAccessDeniedError):
            modules.applications.update_application(
                user_id=other_user.id,
                application_id=app.id,
                name="Hacked Name",
            )

    def test_update_nonexistent_application(self, modules, user_pool):
        """Test updating non-existent application."""
        user = user_pool.get_user()

        with pytest.raises(modules.applications.ApplicationNotFoundError):
            modules.applications.update_application(
                user_id=user.id,
                application_id=999999999,
                name="New Name",
            )


@pytest.mark.applications
@pytest.mark.integration
class TestApplicationDelete:
    """Tests for deleting applications."""

    def test_delete_application(self, modules, app_factory):
        """Test deleting an application."""
        app, owner = app_factory.create()

        result = modules.applications.delete_application(owner.id, app.id)

        assert result is True
        assert modules.applications.get_application(app.id) is None

    def test_delete_application_not_owner(self, modules, test_application, user_pool):
        """Test that non-owner cannot delete application."""
        app, owner = test_application
        other_user = user_pool.get_user()

        with pytest.raises(modules.applications.ApplicationAccessDeniedError):
            modules.applications.delete_application(other_user.id, app.id)

    def test_delete_nonexistent_application(self, modules, user_pool):
        """Test deleting non-existent application."""
        user = user_pool.get_user()

        with pytest.raises(modules.applications.ApplicationNotFoundError):
            modules.applications.delete_application(user.id, 999999999)


@pytest.mark.applications
@pytest.mark.integration
class TestClientSecret:
    """Tests for client secret management."""

    def test_regenerate_client_secret(self, modules, test_application):
        """Test regenerating client secret."""
        app, owner = test_application
        original_secret = app.client_secret

        new_secret = modules.applications.regenerate_client_secret(owner.id, app.id)

        assert new_secret is not None
        assert new_secret != original_secret

    def test_regenerate_secret_not_owner(self, modules, test_application, user_pool):
        """Test that non-owner cannot regenerate secret."""
        app, owner = test_application
        other_user = user_pool.get_user()

        with pytest.raises(modules.applications.ApplicationAccessDeniedError):
            modules.applications.regenerate_client_secret(other_user.id, app.id)
