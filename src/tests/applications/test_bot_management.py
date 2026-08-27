"""Tests for bot approval and request management."""

import pytest
from unittest.mock import patch

from src.core.applications.exceptions import (
    BotRequestError,
    PermissionDeniedError,
)


@pytest.fixture
def app_manager_with_servers(app_manager, server_manager):
    """Attach server permission checks to the application manager."""
    app_manager._servers = server_manager
    return app_manager


@pytest.fixture(autouse=True)
def _enable_bots_license():
    """Enable the bots license feature for the duration of these tests.

    The bot approval flows legitimately gate on ``has_feature("bots")``;
    the suite runs unlicensed by default, so patch the proxy module the
    manager imports (``importlib.import_module("utils.licensing")``) the
    same way the channel-ratchet licensing tests patch it.
    """
    with patch("utils.licensing.has_feature", return_value=True):
        yield


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

    def test_review_bot_request_rejects_already_reviewed(
        self, app_manager_with_servers, server_manager, bot_test_users
    ):
        owner, other = bot_test_users
        server = server_manager.create_server(owner.id, "Bot Server")
        app_manager_with_servers.create_application(other.id, "Bot App")
        request = app_manager_with_servers.request_bot(
            server.id,
            app_manager_with_servers.get_application(
                app_manager_with_servers._db.fetch_one(
                    "SELECT id FROM app_applications WHERE owner_id = ?",
                    (other.id,),
                )["id"]
            ).id,
            other.id,  # requester is other
        )

        # SECURITY: reviewers must be different from the requester (self-review blocked).
        # owner is the server creator and has server.manage; other is the requester.
        app_manager_with_servers.review_bot_request(
            server_id=server.id,
            request_id=request.id,
            reviewer_id=owner.id,
            approve=True,
        )

        with pytest.raises(BotRequestError, match="already approved"):
            app_manager_with_servers.review_bot_request(
                server_id=server.id,
                request_id=request.id,
                reviewer_id=owner.id,
                approve=True,
            )

    def test_last_used_at_populated_on_verify(self, app_manager, test_user):
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="LastUsed App",
            redirect_uris=["https://example.com/callback"],
        )
        from src.core.applications.oauth.flows import OAuth2Flow
        from src.core.applications.oauth.tokens import generate_client_secret

        flow = OAuth2Flow(
            app_manager._db,
            {
                "code_expiry_seconds": 600,
                "token_expiry_seconds": 604800,
                "refresh_enabled": True,
            },
        )
        code = flow.create_authorization_code(
            application_id=app.id,
            user_id=test_user.id,
            redirect_uri="https://example.com/callback",
            scopes=["identify"],
        )
        client_secret, secret_hash = generate_client_secret()
        app_manager._db.execute(
            "UPDATE app_applications SET client_secret_hash = ? WHERE id = ?",
            (secret_hash, app.id),
        )
        token = flow.exchange_code(
            app.id, client_secret, code.code, "https://example.com/callback"
        )

        flow.verify_access_token(token.access_token)

        row = app_manager._db.fetch_one(
            "SELECT last_used_at FROM app_oauth_tokens WHERE id = ?",
            (token.id,),
        )
        assert row is not None
        assert row["last_used_at"] is not None
        assert row["last_used_at"] > 0
