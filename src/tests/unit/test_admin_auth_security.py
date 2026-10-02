"""Unit tests for admin self-service security helpers."""

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

    def test_begin_otp_setup_resets_existing_otp_state(self, mocker):
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
        mocker.patch("src.core.admin.auth.otp_setup_mixin.pyotp", fake_pyotp)

        result = admin_auth.begin_otp_setup(mock_db, 1, "CurrentPass123!")

        assert result.success is True
        assert result.requires_otp_setup is True
        assert result.otp_secret == "BASE32SECRET"
        assert result.challenge_token is not None
        assert mock_db.execute.called

    def test_disable_otp_consumes_plaintext_backup_code_with_tuple_params(self, mocker):
        from src.core.admin import auth as admin_auth

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            {
                "username": "admin",
                "password_hash": "argon",
                "totp_enabled": 1,
                "totp_secret": "BASE32SECRET",
                "totp_secret_encrypted": None,
                "backup_codes": "AAA111,BBB222",
                "backup_codes_hash": None,
            },
            {
                "totp_secret": "BASE32SECRET",
                "totp_secret_encrypted": None,
                "totp_enabled": 1,
                "backup_codes": "AAA111,BBB222",
                "backup_codes_hash": None,
            },
        ]
        mocker.patch(
            "src.core.admin.auth.otp_setup_mixin._verify_admin_password",
            return_value=(True, {"username": "admin"}, ""),
        )
        mocker.patch(
            "src.core.admin.auth.helpers._check_otp_attempt_limit",
            return_value=True,
        )
        fake_totp = MagicMock()
        fake_totp.verify.return_value = False
        mocker.patch(
            "src.core.admin.auth.otp_setup_mixin.pyotp.TOTP",
            return_value=fake_totp,
        )

        class Cursor:
            rowcount = 1

        mock_db.execute.return_value = Cursor()
        success, message = admin_auth.disable_otp(
            mock_db, 1, "CurrentPass123!", "AAA-111"
        )

        assert success is True
        assert message == "OTP disabled"
        backup_update = mock_db.execute.call_args_list[-2]
        assert isinstance(backup_update.args[1], tuple)
        assert backup_update.args[1] == ("BBB222", 1, "AAA111,BBB222")

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

    def test_disable_otp_decrypts_encrypted_secret_round_trip(self, mocker):
        """The stored admin secret is sealed and opened with the same AAD.

        The existing tests only cover the plaintext fallback. This exercises
        the real encrypt/decrypt path so a context drift between
        ``begin_otp_setup`` and ``disable_otp`` is caught here instead of in
        production.
        """
        from src.core.admin import auth as admin_auth
        from src.utils.encryption import encrypt_data

        admin_id = 7
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "totp_secret": None,
            "totp_secret_encrypted": encrypt_data(
                "ROUNDTRIPSECRET", context=f"admin_totp:{admin_id}"
            ),
            "totp_enabled": 1,
            "backup_codes": None,
            "backup_codes_hash": None,
        }
        mocker.patch(
            "src.core.admin.auth.otp_setup_mixin._verify_admin_password",
            return_value=(True, {"username": "admin"}, ""),
        )
        mocker.patch(
            "src.core.admin.auth.helpers._check_otp_attempt_limit",
            return_value=True,
        )
        fake_totp = MagicMock()
        fake_totp.verify.return_value = True
        mocker.patch(
            "src.core.admin.auth.otp_setup_mixin.pyotp.TOTP",
            return_value=fake_totp,
        )

        class Cursor:
            rowcount = 1

        mock_db.execute.return_value = Cursor()
        success, message = admin_auth.disable_otp(
            mock_db, admin_id, "CurrentPass123!", "123456"
        )

        assert success is True
        assert message == "OTP disabled"

    def test_disable_otp_fails_closed_when_secret_context_mismatches(self, mocker):
        """A secret sealed under a different admin id must not be usable."""
        from src.core.admin import auth as admin_auth
        from src.utils.encryption import encrypt_data

        admin_id = 7
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "totp_secret": None,
            # Sealed under the wrong admin id: decryption must fail rather
            # than silently downgrade to the plaintext column.
            "totp_secret_encrypted": encrypt_data(
                "ROUNDTRIPSECRET", context=f"admin_totp:{admin_id + 1}"
            ),
            "totp_enabled": 1,
            "backup_codes": None,
            "backup_codes_hash": None,
        }
        mocker.patch(
            "src.core.admin.auth.otp_setup_mixin._verify_admin_password",
            return_value=(True, {"username": "admin"}, ""),
        )
        mocker.patch(
            "src.core.admin.auth.helpers._check_otp_attempt_limit",
            return_value=True,
        )

        success, message = admin_auth.disable_otp(
            mock_db, admin_id, "CurrentPass123!", "123456"
        )

        assert success is False
        assert message == "OTP unavailable"
