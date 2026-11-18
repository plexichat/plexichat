"""Hardening regressions for verification-token replay and unbounded fields.

* A verification token is stored against a user id, not an address. Changing
  the account's email must therefore retire every outstanding verify_email
  token, otherwise a token minted for a previously controlled address can be
  replayed to mark the *new* address verified without proving control of it.
* ``validate_email`` is an unbounded regex at the manager boundary, so
  register()/update_user() must reject oversized addresses before they are
  blind-indexed, encrypted and persisted.
"""

import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import InvalidEmailError
from src.utils import encryption


def _register(auth_manager, label="tokbound"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    return user, email


def _issued_token(email_sender):
    body = email_sender.send.call_args[0][2]
    return body.split("Verification Token: ")[1].strip()


class TestVerificationTokenReplay:
    def test_email_change_retires_outstanding_verification_token(
        self, db, auth_manager, email_sender
    ):
        manager = auth_manager._get_manager()
        manager.email_sender = email_sender
        user, old_email = _register(auth_manager, "replay")

        # A token for the old (attacker-controlled) address is live.
        assert manager._send_verification_email(user.id, old_email)
        old_token = _issued_token(email_sender)
        assert (
            db.fetch_one(
                "SELECT id FROM auth_email_tokens "
                "WHERE user_id = ? AND token_type = 'verify_email' AND used = 0",
                (user.id,),
            )
            is not None
        )

        email_sender.send.reset_mock()
        new_email = f"victim_{uuid.uuid4().hex[:8]}@test.local"
        auth_manager.update_user(user.id, email=new_email)

        # The old address' token can no longer verify the account.
        assert auth_manager.verify_email(old_token) is False
        assert auth_manager.get_user(user.id).email_verified is False

        # The token delivered to the new address is the only usable one.
        new_token = _issued_token(email_sender)
        assert new_token != old_token
        assert auth_manager.verify_email(new_token) is True
        # Read the persisted flag directly: get_user() serves a 60s cached
        # snapshot that is not refreshed by the verification write.
        assert (
            db.fetch_one("SELECT email_verified FROM auth_users WHERE id = ?", (user.id,))[
                "email_verified"
            ]
            == 1
        )

    def test_reissue_supersedes_previous_verification_token(
        self, db, auth_manager, email_sender
    ):
        manager = auth_manager._get_manager()
        manager.email_sender = email_sender
        user, email = _register(auth_manager, "resend")

        assert manager._send_verification_email(user.id, email)
        first = _issued_token(email_sender)
        assert manager._send_verification_email(user.id, email)
        second = _issued_token(email_sender)
        assert first != second

        # Only the most recently delivered token remains valid.
        assert auth_manager.verify_email(first) is False
        assert auth_manager.verify_email(second) is True


class TestUnboundedRegistrationFields:
    def test_register_rejects_oversized_email(self, db, auth_manager):
        oversized = "a" * 5000 + "@example.com"
        with pytest.raises(InvalidEmailError):
            auth_manager.register(
                username="hugeemailuser",
                email=oversized,
                password="TestPass123!",
            )
        assert (
            db.fetch_one("SELECT id FROM auth_users WHERE username = ?", ("hugeemailuser",))
            is None
        )

    def test_update_user_rejects_oversized_email(self, db, auth_manager):
        user, _email = _register(auth_manager, "bigupd")
        with pytest.raises(InvalidEmailError):
            auth_manager.update_user(user.id, email="b" * 5000 + "@example.com")
        # The stored address must be unchanged after the rejected update.
        assert auth_manager.get_user(user.id).email.endswith("@test.local")
