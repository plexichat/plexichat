"""
Tests for OAuth2 authorization flows.
"""

import pytest


@pytest.mark.applications
@pytest.mark.integration
class TestOAuth2AuthorizationUrl:
    """Tests for OAuth2 authorization URL generation."""

    def test_generate_authorization_url(self, modules, test_application):
        """Test generating authorization URL."""
        app, owner = test_application

        url = modules.applications.generate_oauth_url(
            application_id=app.id,
            redirect_uri="https://example.com/callback",
            scopes=["identify", "guilds"],
        )

        assert f"client_id={app.id}" in url
        assert "redirect_uri=" in url
        assert "scope=" in url
        assert "response_type=code" in url

    def test_generate_url_with_state(self, modules, test_application):
        """Test generating URL with state parameter."""
        app, owner = test_application

        url = modules.applications.generate_oauth_url(
            application_id=app.id,
            redirect_uri="https://example.com/callback",
            scopes=["identify"],
            state="random_state_123",
        )

        assert "state=random_state_123" in url

    def test_generate_url_with_bot_permissions(self, modules, test_application):
        """Test generating URL with bot permissions."""
        app, owner = test_application

        url = modules.applications.generate_oauth_url(
            application_id=app.id,
            redirect_uri="https://example.com/callback",
            scopes=["bot"],
            permissions="8",
        )

        assert "permissions=8" in url


@pytest.mark.applications
@pytest.mark.integration
class TestOAuth2Scopes:
    """Tests for OAuth2 scope validation."""

    def test_validate_valid_scopes(self, modules):
        """Test validating valid scopes."""
        valid, issues = modules.applications.validate_scopes(["identify", "guilds"])

        assert valid is True
        assert len(issues) == 0

    def test_validate_invalid_scopes(self, modules):
        """Test validating invalid scopes."""
        valid, issues = modules.applications.validate_scopes(
            ["identify", "invalid_scope"]
        )

        assert valid is False
        assert len(issues) > 0

    def test_validate_empty_scopes(self, modules):
        """Test validating empty scopes."""
        valid, issues = modules.applications.validate_scopes([])

        assert valid is False

    def test_parse_scope_string(self, modules):
        """Test parsing scope string."""
        scopes = modules.applications.parse_scopes("identify guilds email")

        assert len(scopes) == 3
        assert "identify" in scopes
        assert "guilds" in scopes
        assert "email" in scopes

    def test_scopes_to_string(self, modules):
        """Test converting scopes to string."""
        scope_str = modules.applications.scopes_to_string(
            ["guilds", "identify", "email"]
        )

        assert "identify" in scope_str
        assert "guilds" in scope_str
        assert "email" in scope_str


@pytest.mark.applications
@pytest.mark.integration
class TestOAuth2CodeExchange:
    """Tests for OAuth2 code exchange."""

    def test_exchange_invalid_code(self, modules, test_application):
        """Test exchanging invalid authorization code."""
        app, owner = test_application

        with pytest.raises(modules.applications.InvalidGrantError):
            modules.applications.exchange_code(
                application_id=app.id,
                client_secret=app.client_secret,
                code="invalid_code",
                redirect_uri="https://example.com/callback",
            )

    def test_exchange_invalid_client_secret(self, modules, test_application):
        """Test exchanging with invalid client secret."""
        app, owner = test_application

        with pytest.raises(modules.applications.InvalidClientError):
            modules.applications.exchange_code(
                application_id=app.id,
                client_secret="wrong_secret",
                code="auth.123.abc",
                redirect_uri="https://example.com/callback",
            )


@pytest.mark.applications
@pytest.mark.integration
class TestOAuth2TokenRefresh:
    """Tests for OAuth2 token refresh."""

    def test_refresh_invalid_token(self, modules, test_application):
        """Test refreshing invalid token."""
        app, owner = test_application

        with pytest.raises(modules.applications.InvalidGrantError):
            modules.applications.refresh_token(
                application_id=app.id,
                client_secret=app.client_secret,
                refresh_token_str="invalid_refresh_token",
            )


@pytest.mark.applications
@pytest.mark.integration
class TestOAuth2TokenRevocation:
    """Tests for OAuth2 token revocation."""

    def test_revoke_invalid_token(self, modules):
        """Test revoking invalid token returns False."""
        result = modules.applications.revoke_token("invalid_token")
        assert result is False

    def test_revoke_malformed_token(self, modules):
        """Test revoking malformed token."""
        result = modules.applications.revoke_token("not.a.valid.token.format")
        assert result is False
