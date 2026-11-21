"""Revoking a passkey must retire sessions minted with that credential.

A passkey is a first-factor credential. ``auth_passkeys.revoked`` only stops
*new* logins: ``auth_sessions`` carries no credential-version binding, so a
bearer/cookie token obtained with a passkey (a lost/stolen security key, or a
passkey synced into a compromised account) keeps full access until its natural
expiry after the key has been "revoked". Every other credential transition in
this module -- password change/reset, email change, 2FA enable/disable -- also
revokes sessions; removing a passkey must do the same, otherwise the user's
remediation does not actually kick the attacker out.
"""

import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption

pytestmark = [pytest.mark.auth, pytest.mark.unit]


class _FakePasskeys:
    """Stand-in for the WebAuthn backend: revoke succeeds/fails deterministically."""

    def __init__(self, result: bool):
        self.result = result

    def is_available(self) -> bool:
        return True

    def revoke_passkey(self, user_id: int, passkey_id: int) -> bool:
        return self.result


def _register_with_live_session(auth, username):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth.register(username, f"{username}@example.com", "TestPass123!")
    result = auth.create_session_for_user(user.id)
    assert result.session is not None
    return user, result.session


def test_revoke_passkey_revokes_live_sessions(db, auth_manager, monkeypatch):
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    username = f"pk_revoke_{uuid.uuid4().hex[:8]}"
    user, session = _register_with_live_session(auth_manager, username)

    row = db.fetch_one("SELECT revoked FROM auth_sessions WHERE id = ?", (session.id,))
    assert row["revoked"] == 0, "precondition: the session is live before revocation"

    monkeypatch.setattr(mgr, "passkeys", _FakePasskeys(True))
    assert auth_manager.revoke_passkey(user.id, 424242) is True

    row = db.fetch_one("SELECT revoked FROM auth_sessions WHERE id = ?", (session.id,))
    assert row["revoked"] == 1, (
        "a revoked passkey must invalidate sessions authenticated with it; "
        "flipping the passkey's revoked flag alone leaves the attacker's token live"
    )


def test_failed_passkey_revoke_does_not_touch_sessions(db, auth_manager, monkeypatch):
    """Control: a refused/failed revocation has no side effect."""
    from src.core.auth._lazy import _get_auth_manager

    mgr = _get_auth_manager().get_instance()
    username = f"pk_norevoke_{uuid.uuid4().hex[:8]}"
    user, session = _register_with_live_session(auth_manager, username)

    monkeypatch.setattr(mgr, "passkeys", _FakePasskeys(False))
    assert auth_manager.revoke_passkey(user.id, 424242) is False

    row = db.fetch_one("SELECT revoked FROM auth_sessions WHERE id = ?", (session.id,))
    assert row["revoked"] == 0
