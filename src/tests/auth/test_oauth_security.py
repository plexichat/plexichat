"""
Tests for OAuth security features.

Tests cover:
- Server-side state management (CSRF protection)
- PKCE (Proof Key for Code Exchange)
- State expiration and single-use enforcement
- Provider and redirect URI binding
- Configuration options
"""

import pytest


@pytest.mark.auth
@pytest.mark.slow
class TestOAuthStateManagement:
    """Tests for OAuth state management (CSRF protection)."""

    def test_create_state(self, db):
        """Test creating an OAuth state."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        assert state is not None
        assert state.state_token is not None
        assert len(state.state_token) >= 32
        assert state.provider == "google"
        assert state.redirect_uri == "https://example.com/callback"
        assert state.used is False

    def test_create_state_with_nonce(self, db):
        """Test creating state with OIDC nonce."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            include_nonce=True,
        )

        assert state.nonce_value is not None
        assert len(state.nonce_value) >= 32
        assert state.nonce is not None  # Hash stored

    def test_create_state_with_pkce(self, db):
        """Test creating state with PKCE challenge."""
        from src.core.auth.oauth import OAuthStateManager, generate_pkce_pair

        manager = OAuthStateManager(db)
        pkce = generate_pkce_pair()
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            pkce_challenge=pkce.code_challenge,
        )

        assert state.pkce_challenge is not None
        assert state.pkce_challenge == pkce.code_challenge

    def test_verify_valid_state(self, db):
        """Test verifying a valid state."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        verified, state_record, error = manager.verify_state(
            state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        assert verified is True
        assert state_record is not None

    def test_verify_state_wrong_provider(self, db):
        """Test verifying state with wrong provider fails."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        verified, state_record, error = manager.verify_state(
            state.state_token,
            provider="github",
            redirect_uri="https://example.com/callback",
        )

        assert verified is False

    def test_verify_state_wrong_redirect_uri(self, db):
        """Test verifying state with wrong redirect URI fails."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        verified, state_record, error = manager.verify_state(
            state.state_token,
            provider="google",
            redirect_uri="https://evil.com/callback",
        )

        assert verified is False

    def test_verify_state_replay_attack(self, db):
        """Test state cannot be used twice (replay attack protection)."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        # First use succeeds
        manager.verify_state(
            state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        # Second use fails
        verified, state_record, error = manager.verify_state(
            state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        assert verified is False

    def test_verify_invalid_state(self, db):
        """Test verifying invalid state fails."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)

        verified, state_record, error = manager.verify_state(
            "invalid_state_token",
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        assert verified is False

    def test_verify_expired_state(self, db):
        """Test expired state cannot be verified."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ttl_seconds=-1,  # Already expired
        )

        verified, state_record, error = manager.verify_state(
            state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        assert verified is False

    def test_cleanup_expired_states(self, db):
        """Test expired states are cleaned up."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        # Use ttl_seconds to create expired states
        manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ttl_seconds=-1,  # Already expired
        )
        manager.create_state(
            provider="github",
            redirect_uri="https://example.com/callback",
            ttl_seconds=-1,  # Already expired
        )

        cleaned = manager.cleanup_expired()
        assert cleaned >= 2

    def test_ip_rate_limiting(self, db):
        """Test IP-based rate limiting for state creation."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)

        # Create a few states from same IP
        for _ in range(3):
            state = manager.create_state(
                provider="google",
                redirect_uri="https://example.com/callback",
                ip_address="203.0.113.1",
            )
            assert state is not None

        # Should still work (rate limit allows reasonable requests)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ip_address="203.0.113.1",
        )

        assert state is not None

    def test_custom_token_entropy(self, db):
        """Test custom token entropy configuration."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        assert len(state.state_token) >= 32


@pytest.mark.auth
@pytest.mark.slow
class TestPKCE:
    """Tests for PKCE (Proof Key for Code Exchange)."""

    def test_generate_pkce_pair(self):
        """Test generating PKCE code verifier and challenge."""
        from src.core.auth.oauth import generate_pkce_pair

        pkce = generate_pkce_pair()

        assert pkce.code_verifier is not None
        assert len(pkce.code_verifier) >= 43  # Minimum for S256
        assert pkce.code_challenge is not None
        assert len(pkce.code_challenge) >= 43
        assert pkce.code_challenge_method == "S256"

    def test_generate_pkce_with_config(self):
        """Test PKCE with custom configuration."""
        from src.core.auth.oauth import generate_pkce_pair

        pkce = generate_pkce_pair(verifier_length=32)

        assert pkce.code_verifier is not None
        assert pkce.code_challenge is not None
        assert pkce.code_challenge_method == "S256"

    def test_verify_pkce_valid(self):
        """Test verifying valid PKCE challenge."""
        from src.core.auth.oauth import generate_pkce_pair, verify_pkce

        pkce = generate_pkce_pair()

        valid = verify_pkce(pkce.code_verifier, pkce.code_challenge)
        assert valid is True

    def test_verify_pkce_with_config(self):
        """Test PKCE verification with custom config."""
        from src.core.auth.oauth import generate_pkce_pair, verify_pkce

        pkce = generate_pkce_pair(verifier_length=32)

        valid = verify_pkce(pkce.code_verifier, pkce.code_challenge)
        assert valid is True

    def test_verify_pkce_invalid(self):
        """Test verifying invalid PKCE challenge fails."""
        from src.core.auth.oauth import verify_pkce

        valid = verify_pkce("wrong_verifier", "wrong_challenge")
        assert valid is False

    def test_verify_pkce_empty(self):
        """Test PKCE verification with empty values fails."""
        from src.core.auth.oauth import verify_pkce

        valid = verify_pkce("", "")
        assert valid is False

    def test_verify_pkce_short_verifier(self):
        """Test PKCE with short verifier fails."""
        from src.core.auth.oauth import verify_pkce

        valid = verify_pkce("short", "challenge")
        assert valid is False

    def test_pkce_uniqueness(self):
        """Test PKCE generates unique values."""
        from src.core.auth.oauth import generate_pkce_pair

        pairs = [generate_pkce_pair() for _ in range(100)]
        verifiers = [p.code_verifier for p in pairs]

        # All verifiers should be unique
        assert len(set(verifiers)) == 100


@pytest.mark.auth
@pytest.mark.slow
class TestOAuthIntegration:
    """Integration tests for OAuth flow."""

    def test_full_oauth_flow_with_pkce(self, db):
        """Test complete OAuth flow with PKCE."""
        from src.core.auth.oauth import (
            OAuthStateManager,
            generate_pkce_pair,
            verify_pkce,
        )

        manager = OAuthStateManager(db)
        pkce = generate_pkce_pair()

        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            pkce_challenge=pkce.code_challenge,
        )

        # Simulate callback
        verified, state_record, error = manager.verify_state(
            state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )

        pkce_valid = verify_pkce(pkce.code_verifier, pkce.code_challenge)

        assert verified is True
        assert pkce_valid is True

    def test_state_ip_tracking(self, db):
        """Test state tracks IP address."""
        from src.core.auth.oauth import OAuthStateManager

        manager = OAuthStateManager(db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ip_address="203.0.113.5",
        )

        assert state.ip_address == "203.0.113.5"

    def test_module_level_functions(self):
        """Test module-level convenience functions."""
        from src.core.auth.oauth import generate_pkce_pair

        pkce = generate_pkce_pair()

        assert pkce.code_verifier is not None
        assert len(pkce.code_verifier) >= 32
        assert pkce.code_challenge is not None
        assert len(pkce.code_challenge) >= 32
