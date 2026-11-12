"""Boundary tests for the split session mixin package.

``managers/session.py`` became a package of four mixins. ``AuthManager`` and
callers still import ``SessionMixin`` from the historical path, so these tests
pin the composition, the one-owner-per-method rule, and the class state that
the failure bookkeeping relies on.
"""

import pytest

from src.core.auth.managers.session import SessionMixin
from src.core.auth.managers.session.lifecycle import LifecycleMixin
from src.core.auth.managers.session.login import LoginMixin
from src.core.auth.managers.session.tickets import TicketMixin
from src.core.auth.managers.session.verification import VerificationMixin

OWNERS = {
    LoginMixin: (
        "create_session_for_user",
        "login",
        "_ensure_failed_logins_table",
        "_ip_throttle_active",
        "_clear_failed_login_state",
        "_handle_failed_login",
    ),
    VerificationMixin: ("verify_token", "_verify_session_token", "_verify_bot_token"),
    TicketMixin: (
        "create_ws_ticket_for_session",
        "_ensure_ws_tickets_table",
        "_verify_ws_ticket",
    ),
    LifecycleMixin: (
        "_session_expire_hours",
        "_device_is_trusted_by_id",
        "_create_session",
        "refresh_session",
        "logout",
        "logout_all",
        "logout_all_users",
        "get_sessions",
        "revoke_session",
    ),
}


@pytest.mark.unit
def test_composition_keeps_the_public_import_path():
    """The old module path still exposes one usable SessionMixin."""
    assert issubclass(SessionMixin, LoginMixin)
    assert issubclass(SessionMixin, VerificationMixin)
    assert issubclass(SessionMixin, TicketMixin)
    assert issubclass(SessionMixin, LifecycleMixin)


@pytest.mark.unit
def test_each_method_has_exactly_one_owner():
    """No method is defined twice across the mixins."""
    seen = {}
    for mixin, names in OWNERS.items():
        for name in names:
            assert name in mixin.__dict__, f"{name} belongs on {mixin.__name__}"
            assert name not in seen, f"{name} also defined on {seen[name].__name__}"
            seen[name] = mixin
            # The composition class must not shadow the mixin implementations.
            assert name not in SessionMixin.__dict__


@pytest.mark.unit
def test_failed_login_and_ticket_state_live_on_their_mixins():
    """Class-level guards survive the split (per-process, shared by AuthManager)."""
    # The flags flip once a login or ticket is minted in this process, so check
    # where they live rather than assuming a fresh value under the whole suite.
    assert isinstance(LoginMixin._failed_logins_table_ready, bool)
    assert isinstance(LoginMixin._failed_logins_last_cleanup, int)
    assert isinstance(TicketMixin._ws_tickets_table_ready, bool)
    assert "_failed_logins_table_ready" not in SessionMixin.__dict__
    assert "_ws_tickets_table_ready" not in SessionMixin.__dict__
    assert TicketMixin.WS_TICKET_TTL_MS == 60 * 1000
