"""A recovery address change must retire an outstanding password-reset link.

A reset token is delivered to the e-mail address on file at request time, but
the token row stores only a ``user_id`` -- it is not bound to the address it
was mailed to. ``update_user`` already retires verification tokens and sessions
when the address changes (that is the account-recovery transition that lets a
victim evict an attacker who controls the old inbox), but it deliberately left
password-reset tokens alive. An attacker who captured a reset link sent to the
old address therefore keeps a working account-takeover credential even after
the victim switched to a new, attacker-inaccessible address to reclaim the
account.

Regression: a reset token requested for address A must be dead the moment the
account moves to address B, while an unrelated profile edit leaves it valid.
"""

import re
import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.utils import encryption

_TOKEN_RE = re.compile(r"Token \(fallback\):\s*(\S+)")


class _CapturingSender:
    def __init__(self):
        self.bodies = []

    def send(self, email, subject, body):
        self.bodies.append(body)
        return True

    def tokens(self):
        return [
            m.group(1) for body in self.bodies for m in [_TOKEN_RE.search(body)] if m
        ]


def _register(auth_manager, label):
    name = f"{label}_{uuid.uuid4().hex[:8]}"
    email = f"{name}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(name, email, "TestPass123!")
    return user, email


def _set_sender(auth_manager, sender):
    auth_manager._get_manager().email_sender = sender


@pytest.mark.auth
class TestPasswordResetRetiredOnEmailChange:
    def test_email_change_retires_outstanding_reset_token(self, db, auth_manager):
        user, email = _register(auth_manager, "pr_mail_change")
        sender = _CapturingSender()
        _set_sender(auth_manager, sender)

        assert auth_manager.request_password_reset(email) is True
        (token,) = sender.tokens()
        token_id = int(token.split(".")[1])
        row = db.fetch_one(
            "SELECT used FROM auth_email_tokens WHERE id = ?", (token_id,)
        )
        assert row is not None and int(row["used"]) == 0  # live before the switch

        new_email = f"moved_{uuid.uuid4().hex[:8]}@test.local"
        auth_manager.update_user(user.id, email=new_email)

        row = db.fetch_one(
            "SELECT used FROM auth_email_tokens WHERE id = ?", (token_id,)
        )
        assert row is not None and int(row["used"]) == 1  # retired at the switch

        with pytest.raises(TokenInvalidError):
            auth_manager.reset_password(token, "Brand!NewPass2026")

    def test_non_email_profile_edit_keeps_reset_token(self, db, auth_manager):
        """Control: a username-only edit is not a credential transition."""
        user, email = _register(auth_manager, "pr_user_edit")
        sender = _CapturingSender()
        _set_sender(auth_manager, sender)

        assert auth_manager.request_password_reset(email) is True
        (token,) = sender.tokens()

        auth_manager.update_user(user.id, username=f"renamed_{user.username}")
        assert auth_manager.reset_password(token, "Brand!NewPass2026") is True
