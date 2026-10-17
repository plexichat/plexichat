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

    def test_private_bot_profile_only_resolves_for_the_owner(
        self, app_manager_with_servers, bot_test_users
    ):
        """A private profile must not leak to a direct lookup.

        The directory filters private bots, so answering a direct lookup for
        anyone else would expose the same records anyway.
        """
        owner, other = bot_test_users
        app = app_manager_with_servers.create_application(owner.id, "Private Bot App")
        app_manager_with_servers.update_bot_profile(
            application_id=app.id, user_id=owner.id, private=True
        )

        assert app_manager_with_servers.get_bot_profile(app.id) is None
        assert (
            app_manager_with_servers.get_bot_profile(app.id, viewer_id=other.id) is None
        )
        owner_view = app_manager_with_servers.get_bot_profile(
            app.id, viewer_id=owner.id
        )
        assert owner_view is not None and owner_view.private is True

    def test_public_bot_profile_resolves_for_every_viewer(
        self, app_manager_with_servers, bot_test_users
    ):
        owner, other = bot_test_users
        app = app_manager_with_servers.create_application(owner.id, "Public Bot App")
        app_manager_with_servers.update_bot_profile(
            application_id=app.id, user_id=owner.id, private=False
        )

        for viewer in (None, other.id, owner.id):
            profile = app_manager_with_servers.get_bot_profile(app.id, viewer_id=viewer)
            assert profile is not None and profile.private is False

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


@pytest.mark.applications
class TestAuthorizedAppsExpiryFiltering:
    """M12: expired/revoked OAuth tokens must not be listed as authorized."""

    def _mint_token(self, app_manager, test_user, app):
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
        return flow.exchange_code(
            app.id, client_secret, code.code, "https://example.com/callback"
        )

    def test_valid_token_is_listed(self, app_manager, test_user):
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Authorized App",
            redirect_uris=["https://example.com/callback"],
        )
        token = self._mint_token(app_manager, test_user, app)

        listed = app_manager.get_user_authorized_apps(test_user.id)

        assert [entry.id for entry in listed] == [token.id]

    def test_expired_token_is_not_listed(self, app_manager, test_user):
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Expired Grant App",
            redirect_uris=["https://example.com/callback"],
        )
        token = self._mint_token(app_manager, test_user, app)
        app_manager._db.execute(
            "UPDATE app_oauth_tokens SET expires_at = ? WHERE id = ?",
            (app_manager._get_timestamp() - 1000, token.id),
        )

        assert app_manager.get_user_authorized_apps(test_user.id) == []

    def test_revoked_token_is_not_listed(self, app_manager, test_user):
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Revoked Grant App",
            redirect_uris=["https://example.com/callback"],
        )
        token = self._mint_token(app_manager, test_user, app)
        app_manager._db.execute(
            "UPDATE app_oauth_tokens SET revoked = 1, expires_at = ? WHERE id = ?",
            (app_manager._get_timestamp() + 604800000, token.id),
        )

        assert app_manager.get_user_authorized_apps(test_user.id) == []

    def test_python_layer_drops_rows_past_sql_filter(self):
        """Even if the query returned stale rows, expired (<= now) and
        revoked grants must never surface as authorized."""
        import json
        from unittest.mock import MagicMock

        from src.core.applications.manager.bot_management import BotManagementMixin

        now_ms = 1_700_000_000_000

        class AuthzTestManager(BotManagementMixin):
            def __init__(self, db):
                self._db = db

            def _get_timestamp(self):
                return now_ms

        def row(token_id, expires_at, revoked):
            return {
                "id": token_id,
                "application_id": 10,
                "scopes": json.dumps(["identify"]),
                "created_at": now_ms - 1000,
                "expires_at": expires_at,
                "revoked": revoked,
                "last_used_at": None,
                "app_name": "Test App",
                "app_icon": None,
            }

        db = MagicMock()
        db.fetch_all.return_value = [
            row(1, now_ms + 1000, 0),  # valid
            row(2, now_ms - 1000, 0),  # expired
            row(3, now_ms, 0),  # boundary: expires exactly now
            row(4, now_ms + 1000, 1),  # revoked but unexpired
        ]

        listed = AuthzTestManager(db).get_user_authorized_apps(100)

        assert [entry.id for entry in listed] == [1]
        sql = db.fetch_all.call_args[0][0]
        assert "t.revoked = 0" in sql
        assert "t.expires_at > ?" in sql
