"""Deep hardening: strict session/refresh token grammar and expiry parsing.

``parse_token`` used to be a permissive ``str.split`` + ``int()`` combination.
That accepted signs, leading zeros, unbounded ids and arbitrary short/non
base64url secrets, and let oversized bodies reach hashing/DB work. Expiry
checks compared a raw DB value to the clock, so a malformed expiry could be
treated as "not expired". This suite pins the strict grammar, bounded parsing
and fail-closed expiry behaviour (including on refresh).
"""

import pytest

import src.core.auth.tokens as tokens
from src.core.auth.exceptions import TokenExpiredError, TokenInvalidError


def _secret():
    return tokens.generate_token_secret(32)


def test_minted_tokens_parse():
    for token, kind in [
        (tokens.create_session_token(123)[0], "session"),
        (tokens.create_bot_token(456)[0], "bot"),
        (tokens.create_email_token(789)[0], "email"),
        (tokens.create_2fa_challenge_token(101)[0], "2fa"),
        (tokens.create_ws_ticket(202)[0], "wst"),
    ]:
        parsed = tokens.parse_token(token)
        assert parsed is not None
        assert parsed["token_type"] == kind
        assert parsed["id"] > 0
        assert parsed["secret"] == token.split(".")[-1]


@pytest.mark.parametrize(
    "token",
    [
        None,
        b"123.secret",
        12345,
        "",
        "x" * (tokens.MAX_TOKEN_LENGTH + 1),
        "0." + _secret(),
        "0001." + _secret(),
        "-1." + _secret(),
        "+1." + _secret(),
        "1_2." + _secret(),
        " " + "1." + _secret(),
        "1." + _secret() + "\n",
        "1.short",
        "1." + "!" * tokens.MIN_TOKEN_SECRET_LENGTH,
        "1." + _secret() + ".extra",
        "unknown.1." + _secret(),
    ],
)
def test_malformed_tokens_rejected(token):
    assert tokens.parse_token(token) is None


def test_oversized_and_noncanonical_ids_rejected_on_refresh(auth_manager):
    secret = _secret()
    for bad in [
        "999999999999999999999999." + secret,  # > 19 digits
        "01." + secret,
        "bot.1." + secret,  # wrong type for a refresh
    ]:
        assert auth_manager.refresh_session(bad) is None


@pytest.mark.parametrize(
    "value",
    [None, True, False, 1.5, -1, 0, "abc", "1.5", " 123", "123 ", "", [], "9" * 17],
)
def test_parse_expiry_ms_fails_closed(value):
    assert tokens.parse_expiry_ms(value) is None


def test_parse_expiry_ms_accepts_canonical_values():
    now = 1_700_000_000_000
    assert tokens.parse_expiry_ms(now) == now
    assert tokens.parse_expiry_ms(str(now)) == now
    assert tokens.is_expired(now, now) is True  # expires exactly now is expired
    assert tokens.is_expired(now + 1000, now) is False
    assert tokens.is_expired("garbage", now) is True
    assert tokens.is_expired(None, now) is True


def test_corrupt_expiry_denies_verification(auth_manager, db, test_user_with_token):
    """A malformed stored expiry must invalidate, never extend, a session."""
    from unittest.mock import patch

    with patch("src.utils.encryption.verify_password", return_value=True):
        result = auth_manager.login(
            test_user_with_token["username"], test_user_with_token["password"]
        )
    parsed = tokens.parse_token(result.token)
    assert parsed is not None
    db.execute(
        "UPDATE auth_sessions SET expires_at = ? WHERE id = ?",
        ("not-a-number", parsed["id"]),
    )
    with pytest.raises(TokenExpiredError):
        auth_manager.verify_token(result.token)
    assert auth_manager.refresh_session(result.token) is None


def test_malformed_token_verify_raises_cleanly(auth_manager):
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token("1.short")
