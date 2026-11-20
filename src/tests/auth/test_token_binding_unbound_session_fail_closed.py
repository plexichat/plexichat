"""Regression: IP/UA binding must fail closed for sessions with no stored index.

``_verify_session_token`` only compared the presented IP/UA against the stored
``ip_index``/``ua_index`` when those row fields happened to be truthy. A session
created without a recorded IP or User-Agent (login/2FA/OAuth with no IP, a
legacy row, or a challenge whose IP could not be decrypted) therefore had NO
binding enforced at all, even with ``security.token_binding`` enabled. A stolen
bearer token for such a session could be replayed from any address -- the exact
thing token binding exists to prevent.

These tests pin that a missing stored binding index denies verification while a
correctly bound session still verifies (the checks are hardened, not weakened).
"""

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.database import invalidate_pattern

pytestmark = [pytest.mark.security, pytest.mark.unit]


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    invalidate_pattern("token_verify:*")
    yield
    invalidate_pattern("token_verify:*")


def _enable_binding(auth_manager, monkeypatch):
    manager = auth_manager._manager
    monkeypatch.setattr(
        manager,
        "_config",
        {
            **manager._config,
            "security": {
                **manager._config.get("security", {}),
                "token_binding": True,
            },
        },
    )


def test_unbound_session_token_rejected_from_any_ip_or_ua(
    auth_manager, test_user, monkeypatch
):
    _enable_binding(auth_manager, monkeypatch)

    # Login with no IP/UA -> the session row has NULL ip_index and ua_index.
    token = auth_manager.login(test_user.username, "TestPass123!").token

    # Even a well-formed context cannot verify: there is no stored binding to
    # match, so token binding must deny rather than silently skip the check.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(
            token, ip_address="203.0.113.10", user_agent="legit-agent/1.0"
        )
    # A stolen token replayed from an attacker host is denied too.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(
            token, ip_address="198.51.100.77", user_agent="attacker/9.9"
        )


def test_unbound_session_without_ip_or_ua_denied(auth_manager, test_user, monkeypatch):
    _enable_binding(auth_manager, monkeypatch)

    token = auth_manager.login(test_user.username, "TestPass123!").token

    # Missing presented context is already denied by the early guards; keep
    # that contract alongside the new missing-stored-index guard.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(token)
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(token, ip_address="203.0.113.10")


def test_bound_session_still_verifies_from_same_context(
    auth_manager, test_user, monkeypatch
):
    _enable_binding(auth_manager, monkeypatch)

    result = auth_manager.login(
        test_user.username,
        "TestPass123!",
        ip_address="203.0.113.10",
        user_agent="legit-agent/1.0",
    )
    assert (
        auth_manager.verify_token(
            result.token, ip_address="203.0.113.10", user_agent="legit-agent/1.0"
        ).valid
        is True
    )

    # A different IP/UA is still rejected (existing behaviour preserved).
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(
            result.token, ip_address="198.51.100.77", user_agent="legit-agent/1.0"
        )
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(
            result.token, ip_address="203.0.113.10", user_agent="attacker/9.9"
        )
