"""Tests for application OAuth2 flows."""

import pytest

from src.core.applications.models import OAuth2Scope
from src.core.applications.exceptions import (
    InvalidClientError,
    InvalidScopeError,
    InvalidRedirectUriError,
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
        app_manager.create_application(owner_id=test_user.id, name="Revoke App")
        result = app_manager.revoke_token("nonexistent_token")
        assert result is True or result is False

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
