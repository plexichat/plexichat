"""Tests for application installation integration."""

import pytest

from src.core.applications.exceptions import (
    ApplicationNotFoundError,
    InstallationExistsError,
    InstallationNotFoundError,
)


@pytest.mark.applications
class TestIntegration:
    """Tests for application installation on servers."""

    def test_install_application(self, app_manager, test_user, test_server):
        """Test installing an application on a server."""
        server, owner = test_server
        app = app_manager.create_application(owner_id=test_user.id, name="Install App")
        installation = app_manager.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=test_user.id,
        )
        assert installation.application_id == app.id
        assert installation.server_id == server.id
        assert installation.installer_id == test_user.id

    def test_install_duplicate_raises(self, app_manager, test_user, test_server):
        """Test that installing twice raises error."""
        server, owner = test_server
        app = app_manager.create_application(
            owner_id=test_user.id, name="Dup Install App"
        )
        app_manager.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=test_user.id,
        )
        with pytest.raises(InstallationExistsError):
            app_manager.install_application(
                application_id=app.id,
                server_id=server.id,
                installer_id=test_user.id,
            )

    def test_install_nonexistent_app_raises(self, app_manager, test_user, test_server):
        """Test that installing nonexistent app raises error."""
        server, owner = test_server
        with pytest.raises(ApplicationNotFoundError):
            app_manager.install_application(
                application_id=9999999,
                server_id=server.id,
                installer_id=test_user.id,
            )

    def test_uninstall_application(self, app_manager, test_user, test_server):
        """Test uninstalling an application from a server."""
        server, owner = test_server
        app = app_manager.create_application(
            owner_id=test_user.id, name="Uninstall App"
        )
        app_manager.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=test_user.id,
        )
        result = app_manager.uninstall_application(
            application_id=app.id,
            server_id=server.id,
            user_id=test_user.id,
        )
        assert result is True

    def test_uninstall_not_installed_raises(self, app_manager, test_user, test_server):
        """Test uninstalling a non-installed app raises error."""
        server, owner = test_server
        app = app_manager.create_application(
            owner_id=test_user.id, name="Not Installed App"
        )
        with pytest.raises(InstallationNotFoundError):
            app_manager.uninstall_application(
                application_id=app.id,
                server_id=server.id,
                user_id=test_user.id,
            )

    def test_get_installations_by_app(self, app_manager, test_user, test_server):
        """Test getting installations filtered by application."""
        server, owner = test_server
        app = app_manager.create_application(owner_id=test_user.id, name="Filter App")
        app_manager.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=test_user.id,
        )
        installations = app_manager.get_installations(application_id=app.id)
        assert len(installations) >= 1
        assert all(i.application_id == app.id for i in installations)

    def test_get_installations_by_server(self, app_manager, test_user, test_server):
        """Test getting installations filtered by server."""
        server, owner = test_server
        app = app_manager.create_application(
            owner_id=test_user.id, name="Server Filter App"
        )
        app_manager.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=test_user.id,
        )
        installations = app_manager.get_installations(server_id=server.id)
        assert len(installations) >= 1
        assert all(i.server_id == server.id for i in installations)

    def test_install_with_scopes(self, app_manager, test_user, test_server):
        """Test installing with specific scopes."""
        server, owner = test_server
        app = app_manager.create_application(owner_id=test_user.id, name="Scopes App")
        installation = app_manager.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=test_user.id,
            scopes=["bot", "identify"],
        )
        assert installation.scopes == ["bot", "identify"]
