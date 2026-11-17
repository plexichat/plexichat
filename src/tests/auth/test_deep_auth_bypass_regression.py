"""Security regression suite: authentication-bypass attempts.

Pins the invariants that keep a crafted credential from being promoted into
an authenticated identity:

* token confusion — an email/2FA/gateway token, or a prefix-swapped bearer,
  is never accepted as a session (and direct verifier calls reject a parsed
  dict whose declared family does not match the verifier);
* format/algorithm confusion — unknown prefixes, signed or zero-padded ids,
  unicode lookalike separators, id/secret substitution are all denied;
* expired or already-used reuse — expired sessions/tickets and consumed
  single-use credentials cannot be replayed for access.

This suite is intentionally adversarial: every case must end in a denial or
an expired/consumed error, never a TokenInfo.
"""

from unittest.mock import patch

import pytest

import src.core.auth.tokens as tokens
from src.core.auth.exceptions import TokenExpiredError, TokenInvalidError


def _login(auth_manager, user):
    with patch("src.utils.encryption.verify_password", return_value=True):
        return auth_manager.login(user["username"], user["password"])


def _denied(call, *exc):
    exc = exc or (Exception,)
    with pytest.raises(exc):
        call()


class TestTokenConfusion:
    @pytest.mark.parametrize("factory", ["email", "2fa", "wst"])
    def test_other_token_families_not_accepted_as_session(self, auth_manager, factory):
        secret = tokens.generate_token_secret(32)
        factories = {
            "email": lambda: tokens.create_email_token(999)[0],
            "2fa": lambda: tokens.create_2fa_challenge_token(999)[0],
            "wst": lambda: f"wst.999.{secret}",
        }
        token = factories[factory]()
        _denied(lambda: auth_manager.verify_token(token), TokenInvalidError)

    def test_prefix_swap_on_live_session_denied(
        self, auth_manager, test_user_with_token
    ):
        result = _login(auth_manager, test_user_with_token)
        sid, secret = result.token.split(".", 1)
        for prefix in ("bot", "email", "2fa", "wst"):
            forged = f"{prefix}.{sid}.{secret}"
            _denied(lambda t=forged: auth_manager.verify_token(t), TokenInvalidError)

    def test_direct_verifier_rejects_mismatched_parsed_type(
        self, auth_manager, test_user_with_token
    ):
        manager = auth_manager._manager
        result = _login(auth_manager, test_user_with_token)
        parsed = tokens.parse_token(result.token)
        assert parsed is not None

        wrong = dict(parsed, token_type="bot")
        _denied(lambda: manager._verify_session_token(wrong, None), TokenInvalidError)
        _denied(lambda: manager._verify_bot_token(parsed, None), TokenInvalidError)
        _denied(lambda: manager._verify_ws_ticket(parsed, None), TokenInvalidError)


class TestFormatAndAlgConfusion:
    @pytest.mark.parametrize(
        "forge",
        [
            lambda s: "session" + s[7:] if s.startswith("bot.") else "bot." + s,
            lambda s: s.replace(".", "-", 1),
            lambda s: s + ".",
            lambda s: s + "." + tokens.generate_token_secret(8),
            lambda s: s.replace(".", "\u3002", 1),  # unicode fullwidth full stop
            lambda s: s.replace(s.split(".")[0], "+" + s.split(".")[0], 1),
            lambda s: s.replace(s.split(".")[0], "0" + s.split(".")[0], 1),
        ],
    )
    def test_forged_variants_denied(self, auth_manager, test_user_with_token, forge):
        result = _login(auth_manager, test_user_with_token)
        forged = forge(result.token)
        _denied(lambda: auth_manager.verify_token(forged), TokenInvalidError)

    def test_id_and_secret_substitution_denied(
        self, auth_manager, test_user_with_token
    ):
        result = _login(auth_manager, test_user_with_token)
        sid, secret = result.token.split(".")
        for forged in [
            f"{int(sid) + 1}.{secret}",
            f"{sid}.{tokens.generate_token_secret(32)}",
        ]:
            _denied(lambda t=forged: auth_manager.verify_token(t), TokenInvalidError)

    def test_unknown_algorithm_prefix_denied(self, auth_manager):
        secret = tokens.generate_token_secret(32)
        for prefix in ("jwt", "Bearer", "SESSION", "bot ", ""):
            _denied(
                lambda p=prefix: auth_manager.verify_token(f"{p}.123.{secret}"),
                TokenInvalidError,
            )


class TestExpiredAndUsedReuse:
    def test_expired_session_and_refresh_denied(
        self, auth_manager, db, test_user_with_token
    ):
        result = _login(auth_manager, test_user_with_token)
        parsed = tokens.parse_token(result.token)
        assert parsed is not None
        db.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE id = ?",
            (1, parsed["id"]),
        )
        _denied(lambda: auth_manager.verify_token(result.token), TokenExpiredError)
        assert auth_manager.refresh_session(result.token) is None

    def test_expired_and_reused_ws_ticket_denied(
        self, auth_manager, db, test_user_with_token
    ):
        result = _login(auth_manager, test_user_with_token)
        user = auth_manager.get_user_by_username(test_user_with_token["username"])
        ticket, _ = auth_manager.create_ws_ticket_for_session(
            user.id, result.session.id
        )
        parsed = tokens.parse_token(ticket)
        assert parsed is not None

        # A valid ticket is consumed on first use; a second use is a replay.
        assert auth_manager.verify_token(ticket).valid is True
        _denied(lambda: auth_manager.verify_token(ticket), TokenInvalidError)

        ticket2, _ = auth_manager.create_ws_ticket_for_session(
            user.id, result.session.id
        )
        parsed2 = tokens.parse_token(ticket2)
        assert parsed2 is not None
        db.execute(
            "UPDATE auth_ws_tickets SET expires_at = ? WHERE id = ?", (1, parsed2["id"])
        )
        _denied(lambda: auth_manager.verify_token(ticket2), TokenExpiredError)

    def test_used_2fa_challenge_denied(self, auth_manager, db, test_user_with_token):
        user = auth_manager.get_user_by_username(test_user_with_token["username"])
        manager = auth_manager._manager
        challenge = manager._create_2fa_challenge(user.id, None, None, None)
        db.execute(
            "UPDATE auth_2fa_challenges SET used = 1 WHERE id = ?", (challenge.id,)
        )
        _denied(
            lambda: auth_manager.complete_2fa(challenge.token, "000000"),
            TokenInvalidError,
        )
