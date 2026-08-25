"""W7 lockout-fix regression tests: email-verification HTTP routes and the
verification email sent after an email change.

- POST /api/v1/auth/verify-email {token}
- POST /api/v1/auth/resend-verification {email}
- PATCH email change triggers _send_verification_email for the new address.
"""

import uuid
from unittest.mock import patch

from src.utils import encryption


def _register(auth_manager, label="w7v"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    return user, email


def _attach_sender(auth_manager, email_sender):
    """Attach the mock sender to the underlying AuthManager instance."""
    auth_manager._get_manager().email_sender = email_sender


class TestVerifyEmailRoute:
    def test_valid_token_verifies(self, test_client, db, auth_manager, email_sender):
        _attach_sender(auth_manager, email_sender)
        user, email = _register(auth_manager)
        assert auth_manager._get_manager()._send_verification_email(user.id, email)

        # The raw token is only ever delivered via the verification email.
        body = email_sender.send.call_args[0][2]
        token = body.split("Verification Token: ")[1].strip()

        resp = test_client.post("/api/v1/auth/verify-email", json={"token": token})
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

        check = db.fetch_one(
            "SELECT email_verified FROM auth_users WHERE id = ?", (user.id,)
        )
        assert check["email_verified"] == 1

    def test_invalid_token_is_400(self, test_client, auth_manager):
        resp = test_client.post(
            "/api/v1/auth/verify-email", json={"token": "garbage-token"}
        )
        assert resp.status_code == 400, resp.text
        assert "Invalid or expired" in resp.json()["error"]["message"]

    def test_missing_token_is_400(self, test_client, auth_manager):
        # The app maps request-validation errors to 400.
        resp = test_client.post("/api/v1/auth/verify-email", json={})
        assert resp.status_code == 400, resp.text


class TestResendVerificationRoute:
    def test_existing_unverified_address_sends(
        self, test_client, db, auth_manager, email_sender
    ):
        _attach_sender(auth_manager, email_sender)
        user, email = _register(auth_manager)
        # Force the address unverified so the resend path actually sends.
        db.execute("UPDATE auth_users SET email_verified = 0 WHERE id = ?", (user.id,))
        email_sender.send.reset_mock()

        resp = test_client.post(
            "/api/v1/auth/resend-verification", json={"email": email}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True
        assert email_sender.send.called

    def test_unknown_address_is_enumeration_safe(self, test_client, auth_manager):
        resp = test_client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "ghost_nonexistent@test.local"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True


class TestEmailChangeSendsVerification:
    def test_email_change_sends_new_verification_email(
        self, db, auth_manager, email_sender
    ):
        from src.core import auth as auth_module

        _attach_sender(auth_manager, email_sender)
        user, _old_email = _register(auth_manager, "w7chg")
        email_sender.send.reset_mock()

        new_email = f"w7chg_new_{uuid.uuid4().hex[:8]}@test.local"
        with patch.object(encryption, "verify_password", return_value=True):
            updated = auth_module.update_user(user.id, email=new_email)

        # New address is unverified...
        assert updated.email_verified is False
        # ...and a verification email was actually sent for the change.
        assert email_sender.send.called
        sent_to = email_sender.send.call_args[0][0]
        assert sent_to == new_email

    def test_resubmitting_same_email_does_not_demote_verification(
        self, db, auth_manager, email_sender
    ):
        from src.core import auth as auth_module

        _attach_sender(auth_manager, email_sender)
        user, email = _register(auth_manager, "w7same")
        db.execute("UPDATE auth_users SET email_verified = 1 WHERE id = ?", (user.id,))
        email_sender.send.reset_mock()

        with patch.object(encryption, "verify_password", return_value=True):
            updated = auth_module.update_user(user.id, email=email)

        assert updated.email_verified is True
        assert not email_sender.send.called
