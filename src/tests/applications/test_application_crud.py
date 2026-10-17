"""Tests for application CRUD operations."""

from unittest.mock import patch


class TestApplicationCRUD:
    """Test application create, read, update, delete operations."""

    def test_create_application(self, db, auth_manager, app_manager):
        """Test creating an application."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        app = app_manager.create_application(
            owner_id=user.id,
            name="Test App",
            description="A test application",
        )
        assert app is not None
        assert app.name == "Test App"

        # Verify we can retrieve it
        retrieved = app_manager.get_application(app.id, user.id)
        assert retrieved is not None
        assert retrieved.id == app.id

    def test_get_application(self, db, auth_manager, app_manager):
        """Test retrieving an application."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        app = app_manager.create_application(
            owner_id=user.id,
            name="Test App",
            description="A test application",
        )

        retrieved = app_manager.get_application(app.id, user.id)
        assert retrieved is not None
        assert retrieved.id == app.id

    def test_update_application(self, db, auth_manager, app_manager):
        """Test updating an application."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        app = app_manager.create_application(
            owner_id=user.id,
            name="Test App",
            description="A test application",
        )

        updated = app_manager.update_application(
            user.id, app.id, name="Updated App", description="Updated description"
        )
        assert updated is not None
        assert updated.name == "Updated App"

    def test_delete_application(self, db, auth_manager, app_manager):
        """Test deleting an application."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        app = app_manager.create_application(
            owner_id=user.id,
            name="Test App",
            description="A test application",
        )

        app_manager.delete_application(user.id, app.id)

        # Verify deletion
        retrieved = app_manager.get_application(app.id, user.id)
        assert retrieved is None  # Should return None when not found
