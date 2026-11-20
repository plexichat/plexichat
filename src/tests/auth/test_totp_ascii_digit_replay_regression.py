"""TOTP codes must be ASCII digits, or the one-time code can be replayed.

``str.isdigit()`` accepts Unicode decimal digits (fullwidth "５", Arabic-Indic
"٥", ...). pyotp's verifier accepts some of those spellings as the *same*
numeric code, so an authenticator code submitted as "１２３４５６" verifies even
though "123456" was already consumed. Because the replay cache key is built
from the raw code text, the homoglyph claims a different key and sails through
-- defeating the single-use guarantee for every privileged 2FA operation.

The boundary now pins codes to plain ASCII digits, so every spelling that can
verify maps to one canonical replay key (and non-canonical input fails closed).
"""

from unittest.mock import patch

import pyotp

from src.core.auth import totp as totp_module
from src.core.auth.totp import TOTPReplayCache, verify_totp_code
from src.tests._conftest.totp_clock import frozen_totp_time


def _fullwidth_digits(code: str) -> str:
    """Fullwidth ASCII digit homoglyphs of a 6-digit TOTP code."""
    return "".join(chr(ord(ch) + 0xFF10 - 0x30) for ch in code)


def _fresh_secret() -> str:
    return pyotp.random_base32()


def test_pyotp_accepts_fullwidth_digits_but_boundary_rejects_them():
    """The homoglyph really verifies in pyotp; our gate must stop it."""
    secret = _fresh_secret()
    with frozen_totp_time():
        code = pyotp.TOTP(secret).now()
        fullwidth = _fullwidth_digits(code)
        # Sanity: this is the differential that made the bug exploitable.
        assert pyotp.TOTP(secret).verify(fullwidth, valid_window=1) is True
        # The hardened boundary rejects the non-canonical spelling outright.
        assert verify_totp_code(secret, fullwidth) is False
        assert verify_totp_code(secret, "١٢٣٤٥٦") is False
        # The canonical ASCII form still verifies.
        assert verify_totp_code(secret, code) is True


def test_used_totp_code_cannot_be_replayed_via_unicode_homoglyph(monkeypatch):
    """A consumed one-time code stays consumed under a digit homoglyph."""
    secret = _fresh_secret()
    with frozen_totp_time():
        code = pyotp.TOTP(secret).now()
        fullwidth = _fullwidth_digits(code)

    # Isolate replay state from the process-global cache and any shared backend.
    monkeypatch.setattr(totp_module, "_replay_cache", TOTPReplayCache(ttl_seconds=90))
    user_id = 987_654
    with patch("src.core.database.valkey_client.get_client", return_value=None):
        assert (
            verify_totp_code(
                secret, code, user_id=user_id, purpose="login", acting_user_id=user_id
            )
            is True
        )
        # The exact code is a replay.
        assert (
            verify_totp_code(
                secret, code, user_id=user_id, purpose="login", acting_user_id=user_id
            )
            is False
        )
        # Pre-fix this returned True: the fullwidth spelling claimed a fresh
        # replay key and let the already-used code through again.
        assert (
            verify_totp_code(
                secret,
                fullwidth,
                user_id=user_id,
                purpose="login",
                acting_user_id=user_id,
            )
            is False
        )
