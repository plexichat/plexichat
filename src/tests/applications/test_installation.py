"""Tests for application installation."""

import pytest
from unittest.mock import patch


class TestApplicationInstallation:
    """Test application installation on servers."""

    def test_install_application(self, db, auth_manager, app_manager, server_manager):
        """Test installing an application on a server."""
        from src.utils import encryption

        # Create user and server
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        server = server_manager.create_server(user.id, "Test Server")

        # Create application
        app = app_manager.create_application(
            owner_id=user.id,
            name="Test App",
            description="A test application",
        )

        # Install application on server
        installation = app_manager.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=user.id,
            permissions="8",
            scopes=["bot", "applications.commands"],
        )

        assert installation is not None
        assert installation.application_id == app.id
        assert installation.server_id == server.id
        assert installation.installer_id == user.id
        assert installation.permissions == "8"
        assert "bot" in installation.scopes

    def test_install_application_default_values(
        self, db, auth_manager, app_manager, server_manager
    ):
        """Test installing with default values."""
        from src.utils import encryption

        # Create user and server
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        server = server_manager.create_server(user.id, "Test Server")

        # Create application
        app = app_manager.create_application(
            owner_id=user.id,
            name="Test App",
            description="A test application",
        )

        # Install with defaults
        installation = app_manager.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=user.id,
        )

        assert installation.permissions == "0"
        assert installation.scopes == []

    def test_install_duplicate_fails(
        self, db, auth_manager, app_manager, server_manager
    ):
        """Test that installing twice fails."""
        from src.utils import encryption
        from src.core.applications.exceptions import InstallationExistsError

        # Create user and server
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        server = server_manager.create_server(user.id, "Test Server")

        # Create application
        app = app_manager.create_application(
            owner_id=user.id,
            name="Test App",
            description="A test application",
        )

        # Install first time
        app_manager.install_application(
            application_id=app.id,
            server_id=server.id,
            installer_id=user.id,
        )

        # Try to install again - should fail
        with pytest.raises(InstallationExistsError):
            app_manager.install_application(
                application_id=app.id,
                server_id=server.id,
                installer_id=user.id,
            )

    def test_install_nonexistent_application(
        self, db, auth_manager, app_manager, server_manager
    ):
        """Test installing non-existent application."""
        from src.utils import encryption
        from src.core.applications.exceptions import ApplicationNotFoundError

        # Create user and server
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        server = server_manager.create_server(user.id, "Test Server")

        # Try to install non-existent app
        with pytest.raises(ApplicationNotFoundError):
            app_manager.install_application(
                application_id=999999999,
                server_id=server.id,
                installer_id=user.id,
            )
