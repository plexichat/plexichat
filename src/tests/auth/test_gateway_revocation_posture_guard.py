"""Guard the multi-worker socket-revocation posture.

N813 documented the limitation: ``dispatch_remote_event`` only fanned a
``SECURITY_*`` event out to local connections as a client-side hint, leaving a
revoked user's already-open socket authenticated on every sibling worker. The
follow-up fix closes the sockets server-side on every worker that receives a
trusted revocation event. These guards keep the code and its documentation in
sync: they fail if the remote close handler or its documented posture is lost.
"""

import inspect

import pytest

from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.auth import gateway_revocation

pytestmark = [pytest.mark.security, pytest.mark.unit]


def test_close_user_docstring_documents_cross_worker_enforcement():
    doc = inspect.getdoc(gateway_revocation.close_user_gateway_connections) or ""
    lowered = doc.lower()
    # The close remains worker-local in scope but is now triggered on every
    # worker by the cross-worker revocation event.
    assert "worker-local" in lowered
    assert "sibling worker" in lowered
    assert "revocation event" in lowered


def test_close_session_docstring_documents_cross_worker_close():
    doc = inspect.getdoc(gateway_revocation.close_session_gateway_connections) or ""
    lowered = doc.lower()
    assert "revocation event" in lowered
    assert "session id" in lowered


def test_remote_event_dispatch_has_server_side_socket_close_handler():
    """The cross-worker path closes sockets, not just the client hint."""
    src = inspect.getsource(GatewayDispatcher.dispatch_remote_event)
    for required in (
        "_cross_worker_revocation_target",
        "close_user_connections",
        "close_session_connections",
    ):
        assert required in src, f"dispatch_remote_event lost {required}"
