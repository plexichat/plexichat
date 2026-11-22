"""Regression: gateway tickets must inherit the session's IP/UA binding.

``security.token_binding`` ties an auth session to the IP/User-Agent it was
minted from, and the live verifier fails closed when a bearer is replayed from
a different context. A ``wst.`` ticket is a short-lived single-use bearer
derived from that session and is the *only* credential the gateway handshake
ever sees (the long-lived bearer is deliberately not sent over WS).

``_verify_ws_ticket`` accepted ``ip_address``/``user_agent`` but never used
them. A stolen ticket could therefore be exchanged into an authenticated
socket from any host, laundering the session's binding away -- the gateway
authenticates only once at IDENTIFY/RESUME, so there was no later binding
check to recover.

These tests pin that the ticket exchange is enforced against the bound
session's context, while a correctly matched exchange still works (the check
is hardened, not weakened).
"""

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.database import invalidate_pattern

pytestmark = [pytest.mark.security, pytest.mark.unit]

IP = "203.0.113.10"
UA = "legit-browser/1.0"
OTHER_IP = "198.51.100.77"
OTHER_UA = "attacker/9.9"


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


def _mint_ticket(auth_manager, test_user, ip=IP, ua=UA):
    result = auth_manager.login(
        test_user.username,
        "TestPass123!",
        ip_address=ip,
        user_agent=ua,
    )
    ticket, _ = auth_manager.create_ws_ticket_for_session(
        result.user.id, result.session.id
    )
    return result, ticket


def test_ticket_from_bound_session_verifies_in_same_context(
    auth_manager, test_user, monkeypatch
):
    _enable_binding(auth_manager, monkeypatch)
    result, ticket = _mint_ticket(auth_manager, test_user)

    info = auth_manager.verify_token(ticket, ip_address=IP, user_agent=UA)
    assert info.valid is True
    assert info.user_id == result.user.id
    assert info.session_id == result.session.id


def test_ticket_from_bound_session_rejected_from_other_ip(
    auth_manager, test_user, monkeypatch
):
    _enable_binding(auth_manager, monkeypatch)
    _, ticket = _mint_ticket(auth_manager, test_user)

    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(ticket, ip_address=OTHER_IP, user_agent=UA)


def test_ticket_from_bound_session_rejected_from_other_ua(
    auth_manager, test_user, monkeypatch
):
    _enable_binding(auth_manager, monkeypatch)
    _, ticket = _mint_ticket(auth_manager, test_user)

    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(ticket, ip_address=IP, user_agent=OTHER_UA)


def test_ticket_from_bound_session_rejected_without_context(
    auth_manager, test_user, monkeypatch
):
    _enable_binding(auth_manager, monkeypatch)
    _, ticket = _mint_ticket(auth_manager, test_user)

    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(ticket)
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(ticket, ip_address=IP)


def test_ticket_for_unbound_session_fails_closed(
    auth_manager, test_user, monkeypatch
):
    _enable_binding(auth_manager, monkeypatch)
    # Login with no recorded IP/UA -> a session row with NULL binding indexes.
    result = auth_manager.login(test_user.username, "TestPass123!")
    ticket, _ = auth_manager.create_ws_ticket_for_session(
        result.user.id, result.session.id
    )

    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(ticket, ip_address=IP, user_agent=UA)


def test_ticket_canonicalized_spelling_change_is_denied(
    auth_manager, test_user, monkeypatch
):
    # Session binding hashes the raw presented form on both sides, so even an
    # equivalent IPv4-mapped spelling must not launder a ticket to another host.
    _enable_binding(auth_manager, monkeypatch)
    _, ticket = _mint_ticket(auth_manager, test_user)

    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(
            ticket, ip_address="::ffff:" + IP, user_agent=UA
        )
