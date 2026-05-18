"""Tests for bot approval and request management."""

import pytest
from unittest.mock import patch

from src.core.applications.exceptions import BotRequestError, PermissionDeniedError


@pytest.fixture
def app_manager_with_servers(app_manager, server_manager):
    """Attach server permission checks to the application manager."""
    app_manager._servers = server_manager
    return app_manager


@pytest.fixture
def bot_test_users(auth_manager):
    """Create two explicit users with valid emails for bot management tests."""
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        owner = auth_manager.register(
            "bot_owner", "bot_owner@example.com", "TestPass123!"
        )
        other = auth_manager.register(
            "bot_other", "bot_other@example.com", "TestPass123!"
        )
    return owner, other


@pytest.mark.applications
class TestBotManagement:
    def test_approve_bot_requires_server_manage_permission(
        self, app_manager_with_servers, server_manager, bot_test_users
    ):
        owner, other = bot_test_users
        server = server_manager.create_server(owner.id, "Bot Server")
        app = app_manager_with_servers.create_application(owner.id, "Bot App")

        with pytest.raises(PermissionDeniedError, match="server.manage"):
            app_manager_with_servers.approve_bot(server.id, app.id, other.id)

    def test_remove_approved_bot_requires_server_manage_permission(
        self, app_manager_with_servers, server_manager, bot_test_users
    ):
        owner, other = bot_test_users
        server = server_manager.create_server(owner.id, "Bot Server")
        server_manager.add_member(server.id, other.id)
        app = app_manager_with_servers.create_application(owner.id, "Bot App")

        app_manager_with_servers.approve_bot(server.id, app.id, owner.id)

        with pytest.raises(PermissionDeniedError, match="server.manage"):
            app_manager_with_servers.remove_approved_bot(server.id, app.id, other.id)

    def test_review_bot_request_requires_server_manage_permission(
        self, app_manager_with_servers, server_manager, bot_test_users
    ):
        owner, other = bot_test_users
        server = server_manager.create_server(owner.id, "Bot Server")
        app = app_manager_with_servers.create_application(owner.id, "Bot App")
        request = app_manager_with_servers.request_bot(server.id, app.id, owner.id)

        with pytest.raises(PermissionDeniedError, match="server.manage"):
            app_manager_with_servers.review_bot_request(
                server_id=server.id,
                request_id=request.id,
                reviewer_id=other.id,
                approve=True,
            )

    def test_review_bot_request_rejects_mismatched_server_id(
        self, app_manager_with_servers, server_manager, bot_test_users
    ):
        owner, other = bot_test_users
        server = server_manager.create_server(owner.id, "Bot Server")
        other_server = server_manager.create_server(other.id, "Other Server")
        app = app_manager_with_servers.create_application(owner.id, "Bot App")
        request = app_manager_with_servers.request_bot(server.id, app.id, owner.id)

        with pytest.raises(BotRequestError, match="not found"):
            app_manager_with_servers.review_bot_request(
                server_id=other_server.id,
                request_id=request.id,
                reviewer_id=other.id,
                approve=True,
            )
