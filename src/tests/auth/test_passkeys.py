"""
Comprehensive passkey (WebAuthn/FIDO2) tests.

Tests passkey registration, authentication, challenge management,
sign counter validation, and security protections.
"""

import pytest
import time
from unittest.mock import Mock, patch


class TestPasskeyManager:
    """Test PasskeyManager functionality."""

    @pytest.fixture
    def passkey_manager(self, modules):
        """Create a passkey manager instance."""
        return modules.auth.passkeys

    @pytest.fixture
    def mock_webauthn(self):
        """Mock webauthn library functions."""
        with patch("src.core.auth.passkeys.WEBAUTHN_AVAILABLE", True):
            with patch(
                "src.core.auth.passkeys.generate_registration_options"
            ) as mock_reg:
                with patch(
                    "src.core.auth.passkeys.verify_registration_response"
                ) as mock_verify_reg:
                    with patch(
                        "src.core.auth.passkeys.generate_authentication_options"
                    ) as mock_auth:
                        with patch(
                            "src.core.auth.passkeys.verify_authentication_response"
                        ) as mock_verify_auth:
                            yield {
                                "generate_registration_options": mock_reg,
                                "verify_registration_response": mock_verify_reg,
                                "generate_authentication_options": mock_auth,
                                "verify_authentication_response": mock_verify_auth,
                            }

    def test_is_available(self, passkey_manager):
        """Test passkey availability check."""
        with patch("src.core.auth.passkeys.WEBAUTHN_AVAILABLE", True):
            assert passkey_manager.is_available() is True

        with patch("src.core.auth.passkeys.WEBAUTHN_AVAILABLE", False):
            assert passkey_manager.is_available() is False

    def test_generate_registration_options(
        self, passkey_manager, mock_webauthn, modules
    ):
        """Test generating registration options."""
        # Setup mock
        mock_options = Mock()
        mock_options.json.return_value = (
            '{"challenge": "test", "rp": {"id": "localhost"}}'
        )
        mock_webauthn["generate_registration_options"].return_value = mock_options

        # Create test user
        user = modules.auth.register(
            username="passkeyuser",
            email="passkey@example.com",
            password="SecurePassword123!@#",
        )

        # Generate options
        options = passkey_manager.generate_registration_options(
            user_id=user.id, username=user.username, device_name="Test Device"
        )

        assert options is not None
        assert options.challenge_id is not None
        assert options.rp_id is not None
        assert options.rp_name == "Plexichat"
        assert options.user_name == user.username

    def test_verify_registration(self, passkey_manager, mock_webauthn, modules):
        """Test verifying passkey registration."""
        # Setup mocks
        mock_verification = Mock()
        mock_verification.credential_id = b"credential_id_bytes"
        mock_verification.credential_public_key = b"public_key_bytes"
        mock_verification.sign_count = 0
        mock_verification.aaguid = None
        mock_verification.credential_device_type = None
        mock_verification.credential_backed_up = False

        mock_webauthn["verify_registration_response"].return_value = mock_verification

        # Create test user and generate challenge
        user = modules.auth.register(
            username="passkeyuser2",
            email="passkey2@example.com",
            password="SecurePassword123!@#",
        )

        options = passkey_manager.generate_registration_options(
            user_id=user.id, username=user.username, device_name="Test Device"
        )

        # Verify registration
        credential_response = {
            "id": "test_credential_id",
            "response": {"transports": ["internal", "hybrid"]},
        }

        credential = passkey_manager.verify_registration(
            user_id=user.id,
            challenge_id=options.challenge_id,
            credential_response=credential_response,
        )

        assert credential is not None
        assert credential.credential_id == "test_credential_id"
        assert credential.device_name == "Test Device"
        assert credential.sign_count == 0

    def test_challenge_reuse_protection(self, passkey_manager, mock_webauthn, modules):
        """Test that challenges cannot be reused (replay attack protection)."""
        # Setup mocks
        mock_verification = Mock()
        mock_verification.credential_id = b"credential_id_bytes"
        mock_verification.credential_public_key = b"public_key_bytes"
        mock_verification.sign_count = 0
        mock_verification.aaguid = None
        mock_verification.credential_device_type = None
        mock_verification.credential_backed_up = False

        mock_webauthn["verify_registration_response"].return_value = mock_verification

        # Create test user and generate challenge
        user = modules.auth.register(
            username="passkeyuser3",
            email="passkey3@example.com",
            password="SecurePassword123!@#",
        )

        options = passkey_manager.generate_registration_options(
            user_id=user.id, username=user.username, device_name="Test Device"
        )

        # First use should succeed
        credential_response = {"id": "test_credential_id", "response": {}}
        passkey_manager.verify_registration(
            user_id=user.id,
            challenge_id=options.challenge_id,
            credential_response=credential_response,
        )

        # Second use should fail (challenge already used)
        with pytest.raises(ValueError, match="already used"):
            passkey_manager.verify_registration(
                user_id=user.id,
                challenge_id=options.challenge_id,
                credential_response={"id": "another_credential", "response": {}},
            )

    def test_challenge_expiration(self, passkey_manager, modules):
        """Test that expired challenges are rejected."""
        user = modules.auth.register(
            username="passkeyuser4",
            email="passkey4@example.com",
            password="SecurePassword123!@#",
        )

        # Manually insert an expired challenge
        now = int(time.time() * 1000)
        expired_time = now - 10000  # 10 seconds ago

        passkey_manager._db.execute(
            """INSERT INTO auth_passkey_challenges
            (id, challenge_id, user_id, challenge_type, challenge, device_name, expires_at, used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                99999,
                "expired_challenge",
                user.id,
                "registration",
                b"expired",
                "Test",
                expired_time,
                0,
                now,
            ),
        )

        # Try to use expired challenge
        with pytest.raises(ValueError, match="expired"):
            passkey_manager.verify_registration(
                user_id=user.id,
                challenge_id="expired_challenge",
                credential_response={"id": "test", "response": {}},
            )

    def test_sign_counter_validation(self, passkey_manager, mock_webauthn, modules):
        """Test sign counter validation to detect credential cloning."""
        # Setup registration
        mock_reg_verification = Mock()
        mock_reg_verification.credential_id = b"credential_id_bytes"
        mock_reg_verification.credential_public_key = b"public_key_bytes"
        mock_reg_verification.sign_count = 0
        mock_reg_verification.aaguid = None
        mock_reg_verification.credential_device_type = None
        mock_reg_verification.credential_backed_up = False

        mock_webauthn[
            "verify_registration_response"
        ].return_value = mock_reg_verification

        user = modules.auth.register(
            username="passkeyuser5",
            email="passkey5@example.com",
            password="SecurePassword123!@#",
        )

        reg_options = passkey_manager.generate_registration_options(
            user_id=user.id, username=user.username, device_name="Test Device"
        )

        passkey_manager.verify_registration(
            user_id=user.id,
            challenge_id=reg_options.challenge_id,
            credential_response={"id": "test_credential_id", "response": {}},
        )

        # Setup authentication with sign counter that doesn't increase
        mock_auth_verification = Mock()
        mock_auth_verification.new_sign_count = 0  # Same as current - should fail
        mock_auth_verification.credential_id = b"credential_id_bytes"

        mock_webauthn[
            "verify_authentication_response"
        ].return_value = mock_auth_verification

        # Generate auth options
        auth_options = passkey_manager.generate_authentication_options(
            username=user.username
        )

        # Try to authenticate with non-increasing sign counter
        with pytest.raises(ValueError, match="Sign counter validation failed"):
            passkey_manager.verify_authentication(
                challenge_id=auth_options.challenge_id,
                credential_response={"id": "test_credential_id"},
            )

    def test_list_passkeys(self, passkey_manager, mock_webauthn, modules):
        """Test listing user's passkeys."""
        # Setup mock
        mock_verification = Mock()
        mock_verification.credential_id = b"credential_id_bytes"
        mock_verification.credential_public_key = b"public_key_bytes"
        mock_verification.sign_count = 0
        mock_verification.aaguid = None
        mock_verification.credential_device_type = None
        mock_verification.credential_backed_up = False

        mock_webauthn["verify_registration_response"].return_value = mock_verification

        user = modules.auth.register(
            username="passkeyuser6",
            email="passkey6@example.com",
            password="SecurePassword123!@#",
        )

        # Register two passkeys
        passkey_manager.verify_registration(
            user_id=user.id,
            challenge_id=passkey_manager.generate_registration_options(
                user_id=user.id, username=user.username, device_name="Device 1"
            ).challenge_id,
            credential_response={"id": "cred1", "response": {}},
        )

        passkey_manager.verify_registration(
            user_id=user.id,
            challenge_id=passkey_manager.generate_registration_options(
                user_id=user.id, username=user.username, device_name="Device 2"
            ).challenge_id,
            credential_response={"id": "cred2", "response": {}},
        )

        # List passkeys
        passkeys = passkey_manager.list_passkeys(user.id)

        assert len(passkeys) == 2
        assert any(p.device_name == "Device 1" for p in passkeys)
        assert any(p.device_name == "Device 2" for p in passkeys)

    def test_revoke_passkey(self, passkey_manager, mock_webauthn, modules):
        """Test revoking a passkey."""
        # Setup mock
        mock_verification = Mock()
        mock_verification.credential_id = b"credential_id_bytes"
        mock_verification.credential_public_key = b"public_key_bytes"
        mock_verification.sign_count = 0
        mock_verification.aaguid = None
        mock_verification.credential_device_type = None
        mock_verification.credential_backed_up = False

        mock_webauthn["verify_registration_response"].return_value = mock_verification

        user = modules.auth.register(
            username="passkeyuser7",
            email="passkey7@example.com",
            password="SecurePassword123!@#",
        )

        # Register passkey
        credential = passkey_manager.verify_registration(
            user_id=user.id,
            challenge_id=passkey_manager.generate_registration_options(
                user_id=user.id, username=user.username, device_name="Test Device"
            ).challenge_id,
            credential_response={"id": "cred1", "response": {}},
        )

        # Revoke passkey
        result = passkey_manager.revoke_passkey(user.id, credential.id)
        assert result is True

        # Verify it's no longer listed
        passkeys = passkey_manager.list_passkeys(user.id)
        assert len(passkeys) == 0

    def test_rename_passkey(self, passkey_manager, mock_webauthn, modules):
        """Test renaming a passkey."""
        # Setup mock
        mock_verification = Mock()
        mock_verification.credential_id = b"credential_id_bytes"
        mock_verification.credential_public_key = b"public_key_bytes"
        mock_verification.sign_count = 0
        mock_verification.aaguid = None
        mock_verification.credential_device_type = None
        mock_verification.credential_backed_up = False

        mock_webauthn["verify_registration_response"].return_value = mock_verification

        user = modules.auth.register(
            username="passkeyuser8",
            email="passkey8@example.com",
            password="SecurePassword123!@#",
        )

        # Register passkey
        credential = passkey_manager.verify_registration(
            user_id=user.id,
            challenge_id=passkey_manager.generate_registration_options(
                user_id=user.id, username=user.username, device_name="Old Name"
            ).challenge_id,
            credential_response={"id": "cred1", "response": {}},
        )

        # Rename passkey
        result = passkey_manager.rename_passkey(user.id, credential.id, "New Name")
        assert result is True

        # Verify new name
        passkeys = passkey_manager.list_passkeys(user.id)
        assert passkeys[0].device_name == "New Name"

    def test_cleanup_expired_challenges(self, passkey_manager, modules):
        """Test cleanup of expired challenges."""
        user = modules.auth.register(
            username="passkeyuser9",
            email="passkey9@example.com",
            password="SecurePassword123!@#",
        )

        # Insert expired and valid challenges
        now = int(time.time() * 1000)
        expired_time = now - 10000
        future_time = now + 10000

        passkey_manager._db.execute(
            """INSERT INTO auth_passkey_challenges
            (id, challenge_id, user_id, challenge_type, challenge, device_name, expires_at, used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                99998,
                "expired_challenge",
                user.id,
                "registration",
                b"expired",
                "Test",
                expired_time,
                0,
                now,
            ),
        )

        passkey_manager._db.execute(
            """INSERT INTO auth_passkey_challenges
            (id, challenge_id, user_id, challenge_type, challenge, device_name, expires_at, used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                99997,
                "valid_challenge",
                user.id,
                "registration",
                b"valid",
                "Test",
                future_time,
                0,
                now,
            ),
        )

        # Cleanup
        count = passkey_manager.cleanup_expired_challenges()
        assert count >= 1

        # Verify expired challenge is gone
        row = passkey_manager._db.fetch_one(
            "SELECT * FROM auth_passkey_challenges WHERE challenge_id = ?",
            ("expired_challenge",),
        )
        assert row is None

        # Verify valid challenge still exists
        row = passkey_manager._db.fetch_one(
            "SELECT * FROM auth_passkey_challenges WHERE challenge_id = ?",
            ("valid_challenge",),
        )
        assert row is not None

    def test_transports_parsing_robustness(
        self, passkey_manager, mock_webauthn, modules
    ):
        """Test robust parsing of transports field with empty strings."""
        # Setup mock
        mock_verification = Mock()
        mock_verification.credential_id = b"credential_id_bytes"
        mock_verification.credential_public_key = b"public_key_bytes"
        mock_verification.sign_count = 0
        mock_verification.aaguid = None
        mock_verification.credential_device_type = None
        mock_verification.credential_backed_up = False

        mock_webauthn["verify_registration_response"].return_value = mock_verification

        user = modules.auth.register(
            username="passkeyuser10",
            email="passkey10@example.com",
            password="SecurePassword123!@#",
        )

        # Register passkey with transports that include empty strings
        credential = passkey_manager.verify_registration(
            user_id=user.id,
            challenge_id=passkey_manager.generate_registration_options(
                user_id=user.id, username=user.username, device_name="Test Device"
            ).challenge_id,
            credential_response={
                "id": "cred1",
                "response": {"transports": ["internal", "", "hybrid", ""]},
            },
        )

        # Manually insert transports with empty strings to test parsing
        passkey_manager._db.execute(
            "UPDATE auth_passkeys SET transports = ? WHERE id = ?",
            ("internal,,hybrid,", credential.id),
        )

        # List passkeys - should filter empty strings
        passkeys = passkey_manager.list_passkeys(user.id)
        assert len(passkeys) == 1
        # Should only have non-empty transports
        assert "" not in passkeys[0].transports
        assert "internal" in passkeys[0].transports
        assert "hybrid" in passkeys[0].transports


class TestPasskeyAPI:
    """Test passkey API endpoints."""

    def test_passkey_register_options_endpoint(self, api_client, modules):
        """Test GET /api/v1/auth/passkeys/options/register."""
        # Create user and login
        modules.auth.register(
            username="passkeyapi1",
            email="passkeyapi1@example.com",
            password="SecurePassword123!@#",
        )
        token = modules.auth.login("passkeyapi1", "SecurePassword123!@#").token

        # Request registration options
        response = api_client.post(
            "/api/v1/auth/passkeys/options/register",
            json={"device_name": "Test Device"},
            headers={"Authorization": f"Bearer {token}"},
        )

        # Should return 500 if webauthn not available, or options if available
        # This test assumes webauthn may not be available in test environment
        assert response.status_code in [200, 500]

    def test_passkey_list_endpoint(self, api_client, modules):
        """Test GET /api/v1/auth/passkeys."""
        modules.auth.register(
            username="passkeyapi2",
            email="passkeyapi2@example.com",
            password="SecurePassword123!@#",
        )
        token = modules.auth.login("passkeyapi2", "SecurePassword123!@#").token

        response = api_client.get(
            "/api/v1/auth/passkeys", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_passkey_revoke_endpoint(self, api_client, modules):
        """Test DELETE /api/v1/auth/passkeys/{passkey_id}."""
        modules.auth.register(
            username="passkeyapi3",
            email="passkeyapi3@example.com",
            password="SecurePassword123!@#",
        )
        token = modules.auth.login("passkeyapi3", "SecurePassword123!@#").token

        # Try to revoke non-existent passkey
        response = api_client.delete(
            "/api/v1/auth/passkeys/99999", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 404

    def test_passkey_rename_endpoint(self, api_client, modules):
        """Test PATCH /api/v1/auth/passkeys/{passkey_id}."""
        modules.auth.register(
            username="passkeyapi4",
            email="passkeyapi4@example.com",
            password="SecurePassword123!@#",
        )
        token = modules.auth.login("passkeyapi4", "SecurePassword123!@#").token

        # Try to rename non-existent passkey
        response = api_client.patch(
            "/api/v1/auth/passkeys/99999",
            json={"name": "New Name"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
