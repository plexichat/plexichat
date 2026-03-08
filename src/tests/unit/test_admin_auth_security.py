"""Unit tests for admin self-service security helpers."""

import sys
from unittest.mock import MagicMock


class TestAdminAuthSecurity:
    """Exercise admin password and OTP management helpers directly."""

    def test_get_security_status_counts_backup_codes(self, mocker):
        from src.core.admin import auth as admin_auth

        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "username": "admin",
            "email": "admin@example.com",
            "created_at": 1000,
            "last_login": 2000,
            "totp_enabled": 1,
            "must_setup_otp": 0,
            "backup_codes": "AAA111,BBB222,CCC333",
        }
        mocker.patch("utils.config.get", return_value={"require_otp": True})

        status = admin_auth.get_security_status(mock_db, 1)

        assert status is not None
        assert status.username == "admin"
        assert status.otp_enabled is True
        assert status.backup_codes_remaining == 3

    def test_begin_otp_setup_resets_existing_otp_state(self, mocker, monkeypatch):
        from src.core.admin import auth as admin_auth

        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "username": "admin",
            "email": "admin@example.com",
            "password_hash": "argon",
            "created_at": 1000,
            "last_login": 2000,
            "totp_enabled": 1,
            "must_setup_otp": 0,
            "backup_codes": "AAA111",
        }

        class _FakeTOTP:
            def __init__(self, secret):
                self.secret = secret

            def provisioning_uri(self, name, issuer_name):
                return f"otpauth://totp/{issuer_name}:{name}?secret={self.secret}"

        fake_pyotp = MagicMock()
        fake_pyotp.random_base32.return_value = "BASE32SECRET"
        fake_pyotp.TOTP.side_effect = lambda secret: _FakeTOTP(secret)

        mocker.patch("src.utils.encryption.verify_password", return_value=True)
        monkeypatch.setitem(sys.modules, "pyotp", fake_pyotp)

        result = admin_auth.begin_otp_setup(mock_db, 1, "CurrentPass123!")

        assert result.success is True
        assert result.requires_otp_setup is True
        assert result.otp_secret == "BASE32SECRET"
        assert result.challenge_token is not None
        assert mock_db.execute.called

    def test_regenerate_backup_codes_requires_enabled_otp(self, mocker):
        from src.core.admin import auth as admin_auth

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            {
                "username": "admin",
                "email": "admin@example.com",
                "password_hash": "argon",
                "created_at": 1000,
                "last_login": 2000,
                "totp_enabled": 0,
                "must_setup_otp": 0,
                "backup_codes": "",
            },
            {"totp_enabled": 0},
        ]
        mocker.patch("src.utils.encryption.verify_password", return_value=True)

        success, codes, message = admin_auth.regenerate_backup_codes(
            mock_db, 1, "CurrentPass123!"
        )

        assert success is False
        assert codes == []
        assert "Enable OTP" in message
