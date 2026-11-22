"""Regression: OAuth login must honor ``require_email_verification``.

Password login, passkey authentication and 2FA completion all refuse to mint a
session for an account whose email is still unverified when
``accounts.require_email_verification`` is enabled. OAuth login was the only
authentication path missing that gate, so an OAuth assertion could bypass the
email-verification control and obtain a full session.
"""

import pytest

from src.core.auth.exceptions import EmailNotVerifiedError
from src.core.auth.models import AuthStatus


def _require_verified_email(auth_manager, enabled: bool):
    """Temporarily flip the config on the underlying manager."""
    manager = auth_manager._get_manager()
    old_accounts = manager._config.get("accounts", {})
    manager._config["accounts"] = {
        **old_accounts,
        "require_email_verification": enabled,
    }
    return manager, old_accounts


def test_oauth_login_rejects_unverified_email(auth_manager, db):
    manager, old_accounts = _require_verified_email(auth_manager, True)
    try:
        email = "oauth_gate_unverified@example.com"
        user = auth_manager.register("oauth_gate_unverified", email, "TestPass123!")
        row = db.fetch_one(
            "SELECT email_verified FROM auth_users WHERE id = ?", (user.id,)
        )
        assert not row["email_verified"]

        with pytest.raises(EmailNotVerifiedError):
            auth_manager.oauth_login(
                provider="google",
                external_id="google-oauth-gate-unverified",
                email=email,
                email_verified=True,
            )

        sessions = db.fetch_one(
            "SELECT COUNT(*) AS c FROM auth_sessions WHERE user_id = ?", (user.id,)
        )
        assert sessions["c"] == 0
    finally:
        manager._config["accounts"] = old_accounts


def test_oauth_login_allows_verified_email(auth_manager, db):
    """A verified account must still be able to log in via OAuth."""
    manager, old_accounts = _require_verified_email(auth_manager, True)
    try:
        email = "oauth_gate_verified@example.com"
        user = auth_manager.register("oauth_gate_verified", email, "TestPass123!")
        db.execute("UPDATE auth_users SET email_verified = 1 WHERE id = ?", (user.id,))

        result = auth_manager.oauth_login(
            provider="google",
            external_id="google-oauth-gate-verified",
            email=email,
            email_verified=True,
        )
        assert result.status == AuthStatus.SUCCESS
        assert result.token
    finally:
        manager._config["accounts"] = old_accounts
