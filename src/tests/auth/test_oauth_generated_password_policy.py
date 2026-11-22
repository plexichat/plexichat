"""OAuth auto-registration must always mint a policy-compliant password.

Regression for the ~1-in-5 flake in N687's
``test_recovery_email_change_drops_attacker_oauth_link``: ``oauth_login`` used
to hand ``secrets.token_urlsafe(32)`` straight to ``register``. Its base64url
alphabet has only two "special" glyphs (``-`` and ``_``) and ~26% of draws
contain neither, so the password policy rejected the freshly minted credential
and the OAuth login returned FAILED instead of registering the user.
"""

import re
import uuid

import pytest

from src.core.auth.models import AuthStatus
from src.core.auth.passwords import (
    generate_policy_compliant_password,
    validate_password,
)

pytestmark = pytest.mark.auth

_SPECIAL_RE = re.compile(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\;/\'`~]')


def test_generated_password_always_satisfies_policy():
    """Many independent draws must all pass the real validator."""
    for _ in range(400):
        password = generate_policy_compliant_password()
        result = validate_password(password)
        assert result.valid, (password, result.issues)


def test_generated_password_always_contains_every_required_class():
    """The failure was class-based, so pin each class deterministically."""
    for _ in range(400):
        password = generate_policy_compliant_password()
        assert re.search(r"[A-Z]", password), password
        assert re.search(r"[a-z]", password), password
        assert re.search(r"\d", password), password
        assert _SPECIAL_RE.search(password), password
        assert len(password) >= 12


def test_generated_password_keeps_csprng_strength():
    """Complexity must not come at the cost of a short/weak token."""
    passwords = {generate_policy_compliant_password() for _ in range(50)}
    assert len(passwords) == 50  # every draw is unique
    # token_urlsafe(32) is 43 chars; the fix only appends, never shortens.
    assert all(len(password) >= 43 for password in passwords)


def test_oauth_auto_registration_accepts_token_without_special_chars(
    db, auth_manager, monkeypatch
):
    """Deterministic: even a worst-case raw token registers successfully."""
    import src.core.auth.passwords as passwords_module

    real_token_urlsafe = passwords_module.secrets.token_urlsafe
    state = {"forced": False}

    def force_one_bad_draw(n):
        if not state["forced"]:
            state["forced"] = True
            # Lowercase-only: fails uppercase, digit AND special requirements.
            return "a" * 43
        return real_token_urlsafe(n)

    monkeypatch.setattr(passwords_module.secrets, "token_urlsafe", force_one_bad_draw)

    result = auth_manager.oauth_login(
        provider="google",
        external_id=f"oauth-badtoken-{uuid.uuid4().hex}",
        email=f"oauth_bad_{uuid.uuid4().hex[:8]}@test.local",
        email_verified=True,
    )
    assert state["forced"] is True
    assert result.status == AuthStatus.SUCCESS, getattr(result, "message", None)


def test_oauth_auto_registration_is_stable_across_draws(db, auth_manager):
    """Integration: ~26% of these used to fail with the raw token generator."""
    for i in range(20):
        result = auth_manager.oauth_login(
            provider="google",
            external_id=f"oauth-stable-{uuid.uuid4().hex}",
            email=f"oauth_st_{uuid.uuid4().hex[:8]}@test.local",
            email_verified=True,
        )
        assert result.status == AuthStatus.SUCCESS, (
            i,
            getattr(result, "message", None),
        )
