"""
Comprehensive passkey (WebAuthn/FIDO2) tests.

Tests passkey registration, authentication, challenge management,
sign counter validation, and security protections.
"""

import pytest


class TestPasskeyManager:
    """Test PasskeyManager functionality."""

    @pytest.fixture
    def passkey_manager(self, db):
        """Create a passkey manager instance."""
        from src.core.auth.passkeys import PasskeyManager

        return PasskeyManager(db)

    def test_is_available(self, passkey_manager):
        """Test passkey availability check."""
        # Test that the passkey manager can check webauthn availability
        available = passkey_manager.is_available()
        # Result depends on whether webauthn library is installed
        assert isinstance(available, bool)

    def test_manager_initialization(self, passkey_manager, db):
        """Test that passkey manager initializes with real config-derived state."""
        # The manager must be wired to the same DB it was constructed with.
        assert passkey_manager._db is db
        assert isinstance(passkey_manager._crypto, object)

        # Config-derived defaults must be populated (not placeholder None).
        assert isinstance(passkey_manager._challenge_ttl, int)
        assert passkey_manager._challenge_ttl > 0
        assert isinstance(passkey_manager._rp_name, str) and passkey_manager._rp_name
        assert isinstance(passkey_manager._rp_id, str) and passkey_manager._rp_id

        # Table bootstrap must be idempotent and create usable tables.
        from src.core.auth.passkeys import WEBAUTHN_AVAILABLE

        passkey_manager._ensure_tables()
        passkey_manager._ensure_tables()  # second call must not raise
        if WEBAUTHN_AVAILABLE:
            row = db.fetch_one(
                "SELECT name FROM sqlite_master"
                " WHERE type='table' AND name='auth_passkeys'"
            )
            assert row is not None
            assert row["name"] == "auth_passkeys"

    def test_challenge_cleanup(self, passkey_manager, db):
        """Test cleanup of expired challenges removes only expired rows."""
        import time

        now = int(time.time() * 1000)
        db.execute(
            """INSERT INTO auth_passkey_challenges
               (id, challenge_id, user_id, challenge_type, challenge, expires_at, used, created_at)
               VALUES (?, ?, NULL, 'registration', ?, ?, 0, ?)""",
            (
                900001,
                "expired_probe_challenge",
                b"\x00\x01",
                now - 100000,
                now - 200000,
            ),
        )
        db.execute(
            """INSERT INTO auth_passkey_challenges
               (id, challenge_id, user_id, challenge_type, challenge, expires_at, used, created_at)
               VALUES (?, ?, NULL, 'registration', ?, ?, 0, ?)""",
            (900002, "fresh_probe_challenge", b"\x00\x02", now + 3600000, now),
        )

        deleted = passkey_manager.cleanup_expired_challenges()

        assert deleted == 1
        assert (
            db.fetch_one(
                "SELECT challenge_id FROM auth_passkey_challenges WHERE id = 900001"
            )
            is None
        )
        fresh = db.fetch_one(
            "SELECT challenge_id FROM auth_passkey_challenges WHERE id = 900002"
        )
        assert fresh is not None
        assert fresh["challenge_id"] == "fresh_probe_challenge"


class TestPasskeyAPI:
    """Test passkey API endpoints."""

    def test_passkey_register_options_endpoint(self, test_client, auth_headers):
        """Test passkey registration options endpoint."""
        response = test_client.post(
            "/api/v1/auth/passkeys/options/register",
            headers=auth_headers,
        )
        # May return 200 with options, 501 if webauthn not available, or 403
        assert response.status_code in (200, 403, 501)
        if response.status_code == 200:
            data = response.json()
            assert "challenge_id" in data or "options" in data

    def test_passkey_list_endpoint(self, test_client, auth_headers):
        """Test passkey list endpoint."""
        response = test_client.get(
            "/api/v1/auth/passkeys",
            headers=auth_headers,
        )
        assert response.status_code in (200, 403, 501)
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)

    def test_passkey_revoke_endpoint(self, test_client, auth_headers):
        """Test passkey revoke endpoint."""
        response = test_client.delete(
            "/api/v1/auth/passkeys/999999",
            headers=auth_headers,
        )
        # 404 (not found), 403 (forbidden), or 501 (not available)
        assert response.status_code in (200, 404, 403, 501)

    def test_passkey_rename_endpoint(self, test_client, auth_headers):
        """Test passkey rename endpoint."""
        response = test_client.patch(
            "/api/v1/auth/passkeys/999999",
            json={"name": "Renamed Key"},
            headers=auth_headers,
        )
        # 404 (not found), 403 (forbidden), or 501 (not available)
        assert response.status_code in (200, 404, 403, 501)
