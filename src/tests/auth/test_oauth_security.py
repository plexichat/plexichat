"""
Tests for OAuth security features.

Tests cover:
- Server-side state management (CSRF protection)
- PKCE (Proof Key for Code Exchange)
- State expiration and single-use enforcement
- Provider and redirect URI binding
- Configuration options
"""

import time
import pytest
import secrets


@pytest.mark.auth
class TestOAuthStateManagement:
    """Tests for OAuth state management (CSRF protection)."""

    def test_create_state(self, modules):
        """Test creating an OAuth state."""
        from src.core.auth.oauth import OAuthStateManager
        
        manager = OAuthStateManager(modules.db)
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

    def test_create_state_with_nonce(self, modules):
        """Test creating state with OIDC nonce."""
        from src.core.auth.oauth import OAuthStateManager
        
        manager = OAuthStateManager(modules.db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            include_nonce=True,
        )
        
        assert state.nonce_value is not None
        assert len(state.nonce_value) >= 32
        assert state.nonce is not None  # Hash stored

    def test_create_state_with_pkce(self, modules):
        """Test creating state with PKCE challenge."""
        from src.core.auth.oauth import OAuthStateManager, generate_pkce_pair
        
        pkce = generate_pkce_pair()
        manager = OAuthStateManager(modules.db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            pkce_challenge=pkce.code_challenge,
        )
        
        assert state.pkce_challenge == pkce.code_challenge

    def test_verify_valid_state(self, modules):
        """Test verifying a valid state."""
        from src.core.auth.oauth import OAuthStateManager
        
        manager = OAuthStateManager(modules.db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        
        valid, record, error = manager.verify_state(
            state_token=state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        
        assert valid is True
        assert record is not None
        assert record.used is True
        assert error is None

    def test_verify_state_wrong_provider(self, modules):
        """Test state verification fails with wrong provider."""
        from src.core.auth.oauth import OAuthStateManager
        
        manager = OAuthStateManager(modules.db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        
        valid, record, error = manager.verify_state(
            state_token=state.state_token,
            provider="github",  # Wrong provider
            redirect_uri="https://example.com/callback",
        )
        
        assert valid is False
        assert "provider mismatch" in error.lower()

    def test_verify_state_wrong_redirect_uri(self, modules):
        """Test state verification fails with wrong redirect URI."""
        from src.core.auth.oauth import OAuthStateManager
        
        manager = OAuthStateManager(modules.db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        
        valid, record, error = manager.verify_state(
            state_token=state.state_token,
            provider="google",
            redirect_uri="https://evil.com/callback",  # Wrong URI
        )
        
        assert valid is False
        assert "redirect" in error.lower()

    def test_verify_state_replay_attack(self, modules):
        """Test state cannot be reused (replay attack prevention)."""
        from src.core.auth.oauth import OAuthStateManager
        
        manager = OAuthStateManager(modules.db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        
        # First verification should succeed
        valid1, _, _ = manager.verify_state(
            state_token=state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        assert valid1 is True
        
        # Second verification should fail (replay attack)
        valid2, _, error = manager.verify_state(
            state_token=state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        assert valid2 is False
        assert "already used" in error.lower()

    def test_verify_invalid_state(self, modules):
        """Test verification fails with invalid state token."""
        from src.core.auth.oauth import OAuthStateManager
        
        manager = OAuthStateManager(modules.db)
        
        valid, record, error = manager.verify_state(
            state_token="invalid_state_token",
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        
        assert valid is False
        assert "invalid" in error.lower()

    def test_verify_expired_state(self, modules):
        """Test verification fails with expired state."""
        from src.core.auth.oauth import OAuthStateManager
        
        # Create manager with very short TTL
        manager = OAuthStateManager(modules.db, {"state_ttl_seconds": 0, "cleanup_on_verify": False})
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ttl_seconds=0,  # Immediate expiry
        )
        
        # Wait a tiny bit to ensure expiry
        time.sleep(0.01)
        
        valid, record, error = manager.verify_state(
            state_token=state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        
        assert valid is False
        assert "expired" in error.lower()

    def test_cleanup_expired_states(self, modules):
        """Test cleanup removes expired states."""
        from src.core.auth.oauth import OAuthStateManager
        
        manager = OAuthStateManager(modules.db, {"cleanup_on_verify": False})
        
        # Create an expired state
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ttl_seconds=0,
        )
        
        time.sleep(0.01)
        
        # Cleanup should remove it
        count = manager.cleanup_expired()
        assert count >= 1

    def test_ip_rate_limiting(self, modules):
        """Test IP-based rate limiting for state creation."""
        from src.core.auth.oauth import OAuthStateManager
        
        # Create manager with low rate limit
        manager = OAuthStateManager(modules.db, {"max_states_per_ip": 2, "cleanup_on_verify": False})
        
        # Create states up to limit
        state1 = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ip_address="192.168.1.1",
        )
        assert state1 is not None
        
        state2 = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ip_address="192.168.1.1",
        )
        assert state2 is not None
        
        # Third should be rate limited
        state3 = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ip_address="192.168.1.1",
        )
        assert state3 is None  # Rate limited
        
        # Different IP should work
        state4 = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ip_address="192.168.1.2",
        )
        assert state4 is not None

    def test_custom_token_entropy(self, modules):
        """Test custom token entropy configuration."""
        from src.core.auth.oauth import OAuthStateManager
        
        # Create manager with higher entropy
        manager = OAuthStateManager(modules.db, {
            "state_token_bytes": 64,
            "nonce_token_bytes": 64,
        })
        
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            include_nonce=True,
        )
        
        # Higher entropy should result in longer tokens
        assert len(state.state_token) >= 64
        assert len(state.nonce_value) >= 64


@pytest.mark.auth
class TestPKCE:
    """Tests for PKCE (Proof Key for Code Exchange)."""

    def test_generate_pkce_pair(self):
        """Test generating PKCE code verifier and challenge."""
        from src.core.auth.oauth import generate_pkce_pair
        
        pkce = generate_pkce_pair()
        
        assert pkce.code_verifier is not None
        assert pkce.code_challenge is not None
        assert pkce.code_challenge_method == "S256"
        
        # Verifier should be 43-128 chars per RFC 7636
        assert 43 <= len(pkce.code_verifier) <= 128
        
        # Challenge should be base64url encoded SHA-256 (43 chars without padding)
        assert len(pkce.code_challenge) == 43

    def test_generate_pkce_with_config(self):
        """Test generating PKCE with custom configuration."""
        from src.core.auth.oauth import generate_pkce_pair
        
        config = {"verifier_length": 48}
        pkce = generate_pkce_pair(config=config)
        
        assert pkce.code_verifier is not None
        assert 43 <= len(pkce.code_verifier) <= 128

    def test_verify_pkce_valid(self):
        """Test PKCE verification with valid verifier."""
        from src.core.auth.oauth import generate_pkce_pair, verify_pkce
        
        pkce = generate_pkce_pair()
        
        assert verify_pkce(pkce.code_verifier, pkce.code_challenge) is True

    def test_verify_pkce_with_config(self):
        """Test PKCE verification with custom configuration."""
        from src.core.auth.oauth import generate_pkce_pair, verify_pkce
        
        config = {"min_verifier_length": 43, "max_verifier_length": 128}
        pkce = generate_pkce_pair(config=config)
        
        assert verify_pkce(pkce.code_verifier, pkce.code_challenge, config=config) is True

    def test_verify_pkce_invalid(self):
        """Test PKCE verification fails with wrong verifier."""
        from src.core.auth.oauth import generate_pkce_pair, verify_pkce
        
        pkce = generate_pkce_pair()
        
        # Wrong verifier should fail
        assert verify_pkce("wrong_verifier_" + "x" * 30, pkce.code_challenge) is False

    def test_verify_pkce_empty(self):
        """Test PKCE verification fails with empty values."""
        from src.core.auth.oauth import verify_pkce
        
        assert verify_pkce("", "challenge") is False
        assert verify_pkce("verifier" + "x" * 40, "") is False
        assert verify_pkce("", "") is False

    def test_verify_pkce_short_verifier(self):
        """Test PKCE verification fails with too short verifier."""
        from src.core.auth.oauth import verify_pkce
        
        # Verifier must be at least 43 chars
        assert verify_pkce("short", "challenge") is False

    def test_pkce_uniqueness(self):
        """Test that each PKCE pair is unique."""
        from src.core.auth.oauth import generate_pkce_pair
        
        pairs = [generate_pkce_pair() for _ in range(10)]
        verifiers = [p.code_verifier for p in pairs]
        challenges = [p.code_challenge for p in pairs]
        
        # All should be unique
        assert len(set(verifiers)) == 10
        assert len(set(challenges)) == 10


@pytest.mark.auth
class TestOAuthIntegration:
    """Integration tests for OAuth security features."""

    def test_full_oauth_flow_with_pkce(self, modules):
        """Test complete OAuth flow with PKCE."""
        from src.core.auth.oauth import OAuthStateManager, generate_pkce_pair, verify_pkce
        
        manager = OAuthStateManager(modules.db)
        
        # 1. Generate PKCE pair
        pkce = generate_pkce_pair()
        
        # 2. Create state with PKCE challenge
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            include_nonce=True,
            pkce_challenge=pkce.code_challenge,
            ip_address="127.0.0.1",
        )
        
        assert state.state_token is not None
        assert state.nonce_value is not None
        
        # 3. Verify state on callback
        valid, record, error = manager.verify_state(
            state_token=state.state_token,
            provider="google",
            redirect_uri="https://example.com/callback",
        )
        
        assert valid is True
        assert record.pkce_challenge == pkce.code_challenge
        
        # 4. Verify PKCE
        assert verify_pkce(pkce.code_verifier, record.pkce_challenge) is True

    def test_state_ip_tracking(self, modules):
        """Test that IP address is tracked with state."""
        from src.core.auth.oauth import OAuthStateManager
        
        manager = OAuthStateManager(modules.db)
        state = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/callback",
            ip_address="192.168.1.100",
        )
        
        assert state.ip_address == "192.168.1.100"

    def test_module_level_functions(self, modules):
        """Test module-level convenience functions."""
        from src.core.auth.oauth import state as oauth_state
        
        # Setup should have been called by auth.setup()
        # But we'll call it again to ensure it's initialized
        oauth_state.setup(modules.db, {})
        
        # Test create_oauth_state
        state = oauth_state.create_oauth_state(
            provider="github",
            redirect_uri="https://example.com/callback",
        )
        assert state is not None
        
        # Test verify_oauth_state
        valid, record, error = oauth_state.verify_oauth_state(
            state_token=state.state_token,
            provider="github",
            redirect_uri="https://example.com/callback",
        )
        assert valid is True
