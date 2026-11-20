"""The public resend-verification route must not be a mail-bomb primitive.

``request_password_reset`` caps how many reset emails a single address can
trigger; the resend-verification path had no equivalent cap, so replaying the
public request could flood an unverified account's inbox. The throttle must
hold without leaking account state through the boolean result.
"""

from unittest.mock import patch

from src.core.auth._lazy import _get_auth_manager
from src.utils import encryption


def _manager():
    return _get_auth_manager().get_instance()


def _unverified_user(db, auth_manager, username, email):
    # Keep the registration email out of the send count; the throttle counts
    # verification tokens, and registration creates none with verification off.
    _manager().email_sender = None
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(username, email, "Password123!")
    db.execute("UPDATE auth_users SET email_verified = 0 WHERE id = ?", (user.id,))
    return user


def test_resend_verification_is_throttled(db, auth_manager, email_sender):
    email = "resend-throttle@example.com"
    _unverified_user(db, auth_manager, "resend_throttle_user", email)
    _manager().email_sender = email_sender

    for _ in range(6):
        # Enumeration-safe: the result never reveals the throttle state.
        assert auth_manager.resend_verification(email) is True

    assert email_sender.send.call_count == 3


def test_resend_verification_throttle_is_per_email(db, auth_manager, email_sender):
    first = "resend-throttle-a@example.com"
    second = "resend-throttle-b@example.com"
    _unverified_user(db, auth_manager, "resend_throttle_a", first)
    _unverified_user(db, auth_manager, "resend_throttle_b", second)
    _manager().email_sender = email_sender

    # Saturate the first address.
    for _ in range(3):
        auth_manager.resend_verification(first)
    assert email_sender.send.call_count == 3

    # A different address keeps its own budget.
    assert auth_manager.resend_verification(second) is True
    assert email_sender.send.call_count == 4
