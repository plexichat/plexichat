"""Guard the documented multi-worker socket-revocation posture.

N813 found a committed docstring on ``close_user_gateway_connections`` that
falsely claimed sibling workers close sockets via a cross-worker revocation
event. No such handler exists: ``dispatch_remote_event`` only fans the event
out to local connections, and live dispatch re-checks channel/DM membership --
not session validity/ban state. Closure is worker-local; the cross-worker
``SECURITY_LOGOUT``/``SECURITY_ALERT`` events are client-side hints.

These guards keep the documentation honest: if the false claim is reintroduced,
or a remote-event close handler appears without updating the docs, these fail.
"""

import inspect

import pytest

from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.auth import gateway_revocation

pytestmark = [pytest.mark.security, pytest.mark.unit]


def test_close_user_docstring_states_worker_local_limitation():
    doc = inspect.getdoc(gateway_revocation.close_user_gateway_connections) or ""
    lowered = doc.lower()
    # The false claim must stay gone.
    assert "closed on sibling workers" not in lowered
    assert "cross-worker revocation event" not in lowered
    # The accurate posture must be stated explicitly.
    assert "worker-local" in lowered
    assert "client-side hints" in lowered


def test_close_session_docstring_does_not_claim_cross_worker_closure():
    doc = inspect.getdoc(gateway_revocation.close_session_gateway_connections) or ""
    lowered = doc.lower()
    assert "sibling workers" not in lowered
    assert "cross-worker" not in lowered


def test_remote_event_dispatch_has_no_socket_close_handler():
    """The cross-worker event path is a client hint, not a socket revoke."""
    src = inspect.getsource(GatewayDispatcher.dispatch_remote_event)
    for forbidden in (
        "close_user_connections",
        "close_session_connections",
        "close_all_connections",
    ):
        assert forbidden not in src, (
            f"dispatch_remote_event gained a server-side close ({forbidden}); "
            "update the gateway_revocation docstring and docs/websocket/events.md"
        )
