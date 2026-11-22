"""Regression: the LifecycleMixin mirror of ``_verify_ws_ticket`` must also
enforce the session's IP/User-Agent binding.

``TicketMixin._verify_ws_ticket`` is MRO-first and now enforces binding, but
the historical mirror in ``LifecycleMixin`` was left unchecked. It is still
reachable via a future MRO reorder or any direct caller, so leaving it weak
leaves the binding launderable. These tests call the mirror *directly* (bypassing
MRO dispatch) so the check cannot be masked by the active mixin: a ticket
replayed from another IP/User-Agent must be rejected, a matched exchange must
still work, and a rejected context must not burn the ticket.
"""

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.managers.session.lifecycle import LifecycleMixin
from src.core.auth.tokens import parse_token
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
    return result, parse_token(ticket)


def _verify_mirror(auth_manager, parsed, ip=IP, ua=UA):
    # Direct unbound call picks LifecycleMixin's copy regardless of MRO, so the
    # test is non-vacuous: it fails while that mirror ignores the bound indexes.
    return LifecycleMixin._verify_ws_ticket(
        auth_manager._manager, parsed, ip, user_agent=ua
    )


def test_mirror_rejects_other_ip(auth_manager, test_user, monkeypatch):
    _enable_binding(auth_manager, monkeypatch)
    _, parsed = _mint_ticket(auth_manager, test_user)

    with pytest.raises(TokenInvalidError):
        _verify_mirror(auth_manager, parsed, ip=OTHER_IP, ua=UA)


def test_mirror_rejects_other_ua(auth_manager, test_user, monkeypatch):
    _enable_binding(auth_manager, monkeypatch)
    _, parsed = _mint_ticket(auth_manager, test_user)

    with pytest.raises(TokenInvalidError):
        _verify_mirror(auth_manager, parsed, ip=IP, ua=OTHER_UA)


def test_mirror_accepts_matching_context(auth_manager, test_user, monkeypatch):
    _enable_binding(auth_manager, monkeypatch)
    result, parsed = _mint_ticket(auth_manager, test_user)

    info = _verify_mirror(auth_manager, parsed, ip=IP, ua=UA)
    assert info.valid is True
    assert info.session_id == result.session.id


def test_mirror_rejected_context_does_not_burn_ticket(
    auth_manager, test_user, monkeypatch
):
    _enable_binding(auth_manager, monkeypatch)
    result, parsed = _mint_ticket(auth_manager, test_user)

    with pytest.raises(TokenInvalidError):
        _verify_mirror(auth_manager, parsed, ip=OTHER_IP, ua=UA)
    # Rejection must happen before single-use consumption: the legitimate
    # holder can still exchange the very same ticket afterwards.
    info = _verify_mirror(auth_manager, parsed, ip=IP, ua=UA)
    assert info.valid is True
    assert info.session_id == result.session.id


def test_mirror_fails_closed_without_context(auth_manager, test_user, monkeypatch):
    _enable_binding(auth_manager, monkeypatch)
    _, parsed = _mint_ticket(auth_manager, test_user)

    with pytest.raises(TokenInvalidError):
        _verify_mirror(auth_manager, parsed, ip=None, ua=UA)
    with pytest.raises(TokenInvalidError):
        _verify_mirror(auth_manager, parsed, ip=IP, ua=None)


def test_mirror_fails_closed_for_unbound_session(
    auth_manager, test_user, monkeypatch
):
    _enable_binding(auth_manager, monkeypatch)
    # No IP/UA at login -> session row with NULL binding indexes.
    result = auth_manager.login(test_user.username, "TestPass123!")
    ticket, _ = auth_manager.create_ws_ticket_for_session(
        result.user.id, result.session.id
    )
    parsed = parse_token(ticket)

    with pytest.raises(TokenInvalidError):
        _verify_mirror(auth_manager, parsed, ip=IP, ua=UA)
