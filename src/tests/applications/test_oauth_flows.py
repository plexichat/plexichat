"""Tests for application OAuth2 flows."""

import pytest

from src.core.applications.models import OAuth2Scope
from src.core.applications.exceptions import (
    InvalidClientError,
    InvalidScopeError,
    InvalidRedirectUriError,
    InvalidGrantError,
)


@pytest.mark.applications
class TestOAuthFlows:
    """Tests for OAuth2 authorization and token flows."""

    def test_oauth2_scopes_exist(self):
        """Test all OAuth2 scopes are defined."""
        assert OAuth2Scope.IDENTIFY.value == "identify"
        assert OAuth2Scope.EMAIL.value == "email"
        assert OAuth2Scope.GUILDS.value == "guilds"
        assert OAuth2Scope.BOT.value == "bot"
        assert OAuth2Scope.APPLICATIONS_COMMANDS.value == "applications.commands"
        assert OAuth2Scope.MESSAGES_READ.value == "messages.read"
        assert OAuth2Scope.WEBHOOK_INCOMING.value == "webhook.incoming"

    def test_generate_oauth_url(self, app_manager, test_user):
        """Test generating an OAuth2 authorization URL."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="OAuth App",
            redirect_uris=["https://example.com/callback"],
        )
        url = app_manager.generate_oauth_url(
            application_id=app.id,
            redirect_uri="https://example.com/callback",
            scopes=["identify", "email"],
        )
        assert "oauth2" in url.lower() or "authorize" in url.lower()
        assert str(app.id) in url

    def test_generate_oauth_url_with_state(self, app_manager, test_user):
        """Test generating OAuth URL with state parameter."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="State App",
            redirect_uris=["https://example.com/callback"],
        )
        url = app_manager.generate_oauth_url(
            application_id=app.id,
            redirect_uri="https://example.com/callback",
            scopes=["identify"],
            state="random_state_123",
        )
        assert "random_state_123" in url

    def test_revoke_token(self, app_manager, test_user):
        """Test revoking an OAuth2 token."""
        app = app_manager.create_application(owner_id=test_user.id, name="Revoke App")
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

        # Revoking a real token returns True and marks it revoked.
        assert app_manager.revoke_token(token.access_token) is True
        row = app_manager._db.fetch_one(
            "SELECT revoked FROM app_oauth_tokens WHERE id = ?", (token.id,)
        )
        assert row is not None
        assert row["revoked"] == 1

        # Revoking garbage (unparseable token) returns False.
        assert app_manager.revoke_token("not-a-token") is False

    def test_invalid_client_error(self):
        """Test InvalidClientError has correct error field."""
        err = InvalidClientError()
        assert err.error == "invalid_client"

    def test_invalid_scope_error(self):
        """Test InvalidScopeError has correct error field."""
        err = InvalidScopeError()
        assert err.error == "invalid_scope"

    def test_invalid_redirect_uri_error(self):
        """Test InvalidRedirectUriError has correct error field."""
        err = InvalidRedirectUriError()
        assert err.error == "invalid_request"

    def test_code_expiry_is_in_milliseconds(self, app_manager, test_user):
        """Test that authorization code expiry is in milliseconds, not seconds."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Expiry App",
            redirect_uris=["https://example.com/callback"],
        )
        from src.core.applications.oauth.flows import OAuth2Flow

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
        now = flow._current_time()
        remaining = code.expires_at - now
        assert remaining > 500000, (
            f"Code expiry too short: {remaining}ms (should be ~600000ms)"
        )

    def test_token_expiry_is_in_milliseconds(self, app_manager, test_user):
        """Test that token expiry is in milliseconds, not seconds."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="TokenExpiry App",
            redirect_uris=["https://example.com/callback"],
        )
        from src.core.applications.oauth.flows import OAuth2Flow

        flow = OAuth2Flow(
            app_manager._db,
            {
                "code_expiry_seconds": 600,
                "token_expiry_seconds": 604800,
                "refresh_enabled": True,
            },
        )
        token = flow._create_token(
            application_id=app.id,
            user_id=test_user.id,
            scopes=["identify"],
        )
        now = flow._current_time()
        remaining = token.expires_at - now
        assert remaining > 60000000, (
            f"Token expiry too short: {remaining}ms (should be ~604800000ms)"
        )

    def test_exchange_code_rejects_reuse(self, app_manager, test_user):
        """Test that exchanging a code twice fails on second attempt."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Reuse App",
            redirect_uris=["https://example.com/callback"],
        )
        from src.core.applications.oauth.flows import OAuth2Flow

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
        from src.core.applications.oauth.tokens import hash_token

        client_secret = "test_secret"

        app_manager._db.execute(
            "UPDATE app_applications SET client_secret_hash = ? WHERE id = ?",
            (hash_token(client_secret), app.id),
        )

        token1 = flow.exchange_code(
            app.id, client_secret, code.code, "https://example.com/callback"
        )
        assert token1 is not None
        assert token1.access_token is not None

        with pytest.raises(InvalidGrantError, match="already used"):
            flow.exchange_code(
                app.id, client_secret, code.code, "https://example.com/callback"
            )

    def test_expires_in_is_in_seconds(self, app_manager, test_user):
        """Test that expires_in in response is in seconds, not milliseconds."""
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="ExpiresIn App",
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
        assert token is not None
        assert token.get("access_token")

        # The flow call above consumes the code, so issue a fresh one for
        # the manager-level exchange under test.
        code2 = flow.create_authorization_code(
            application_id=app.id,
            user_id=test_user.id,
            redirect_uri="https://example.com/callback",
            scopes=["identify"],
        )
        result = app_manager.exchange_code(
            app.id, client_secret, code2.code, "https://example.com/callback"
        )
        expires_in = result["expires_in"]
        assert expires_in > 500, (
            f"expires_in too small: {expires_in} (should be seconds, ~604800)"
        )
        assert expires_in < 700000, (
            f"expires_in too large: {expires_in} (should be seconds, not ms)"
        )
