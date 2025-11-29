"""
Tests for application installation tracking.
"""

import pytest


@pytest.mark.applications
@pytest.mark.integration
class TestApplicationInstallation:
    """Tests for installing applications on servers."""

    def test_install_application(self, modules, test_application, test_server, user_pool):
        """Test installing an application on a server."""
        app, owner = test_application
        server, server_owner = test_server
        installer = user_pool.get_user()

        installation = modules.applications.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=installer.id,
            permissions="8",
            scopes=["bot", "applications.commands"],
        )

        assert installation.id is not None
        assert installation.application_id == app.id
        assert installation.server_id == server.id
        assert installation.installer_id == installer.id
        assert installation.permissions == "8"
        assert "bot" in installation.scopes

    def test_install_application_default_values(self, modules, test_application, test_server, user_pool):
        """Test installing with default values."""
        app, owner = test_application
        server, server_owner = test_server
        installer = user_pool.get_user()

        installation = modules.applications.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=installer.id,
        )

        assert installation.permissions == "0"
        assert installation.scopes == []

    def test_install_duplicate_fails(self, modules, test_application, test_server, user_pool):
        """Test that installing twice fails."""
        app, owner = test_application
        server, server_owner = test_server
        installer = user_pool.get_user()

        modules.applications.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=installer.id,
        )

        with pytest.raises(modules.applications.InstallationExistsError):
            modules.applications.install_application(
                application_id=app.id,
                server_id=server.id,
                installer_id=installer.id,
            )

    def test_install_nonexistent_application(self, modules, test_server, user_pool):
        """Test installing non-existent application."""
        server, server_owner = test_server
        installer = user_pool.get_user()

        with pytest.raises(modules.applications.ApplicationNotFoundError):
            modules.applications.install_application(
                application_id=999999999,
                server_id=server.id,
                installer_id=installer.id,
            )


@pytest.mark.applications
@pytest.mark.integration
class TestApplicationUninstallation:
    """Tests for uninstalling applications."""

    def test_uninstall_application(self, modules, test_application, test_server, user_pool):
        """Test uninstalling an application."""
        app, owner = test_application
        server, server_owner = test_server
        installer = user_pool.get_user()

        modules.applications.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=installer.id,
        )

        result = modules.applications.uninstall_application(
            application_id=app.id,
            server_id=server.id,
            user_id=installer.id,
        )

        assert result is True

        installations = modules.applications.get_installations(
            application_id=app.id,
            server_id=server.id,
        )
        assert len(installations) == 0

    def test_uninstall_not_installed(self, modules, test_application, test_server, user_pool):
        """Test uninstalling when not installed."""
        app, owner = test_application
        server, server_owner = test_server
        user = user_pool.get_user()

        with pytest.raises(modules.applications.InstallationNotFoundError):
            modules.applications.uninstall_application(
                application_id=app.id,
                server_id=server.id,
                user_id=user.id,
            )


@pytest.mark.applications
@pytest.mark.integration
class TestInstallationRetrieval:
    """Tests for retrieving installations."""

    def test_get_installations_by_application(self, modules, test_application, user_pool):
        """Test getting installations by application."""
        app, owner = test_application

        server1, _ = modules.servers.create_server(owner.id, "Server 1"), owner
        server2, _ = modules.servers.create_server(owner.id, "Server 2"), owner

        modules.applications.install_application(app.id, server1.id, owner.id)
        modules.applications.install_application(app.id, server2.id, owner.id)

        installations = modules.applications.get_installations(application_id=app.id)

        assert len(installations) >= 2

    def test_get_installations_by_server(self, modules, test_server, app_factory, user_pool):
        """Test getting installations by server."""
        server, server_owner = test_server
        installer = user_pool.get_user()

        app1, _ = app_factory.create()
        app2, _ = app_factory.create()

        modules.applications.install_application(app1.id, server.id, installer.id)
        modules.applications.install_application(app2.id, server.id, installer.id)

        installations = modules.applications.get_installations(server_id=server.id)

        assert len(installations) >= 2

    def test_get_specific_installation(self, modules, test_application, test_server, user_pool):
        """Test getting specific installation."""
        app, owner = test_application
        server, server_owner = test_server
        installer = user_pool.get_user()

        modules.applications.install_application(app.id, server.id, installer.id)

        installations = modules.applications.get_installations(
            application_id=app.id,
            server_id=server.id,
        )

        assert len(installations) == 1
        assert installations[0].application_id == app.id
        assert installations[0].server_id == server.id
