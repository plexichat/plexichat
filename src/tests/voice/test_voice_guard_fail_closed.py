"""Regression: voice authorization guards must fail closed on lookup errors.

``_require_connect_permission`` (voice.connect) and ``_require_stream_permission``
(voice.speak) gate credential issuance and screen-share streaming. Both used to
``return`` when the channel lookup raised -- i.e. an unevaluable guard silently
allowed the operation, even though the same guards deny when their helper
objects are missing. A transient DB/row error must never be enough to bypass
voice authorization.

The targeted change is narrow: only the lookup *exception* path now denies.
A successful lookup that returns no row is still deferred to the caller's own
channel lookup (DM voice channels rely on that), and a real channel row is
still checked with exactly the same permission arguments as before.
"""

import pytest

from src.core.voice.exceptions import PermissionDeniedError
from src.core.voice.signaling.manager.connection_mixin import (
    ConnectionManagementMixin,
)
from src.core.voice.signaling.manager.screenshare_mixin import ScreenShareMixin


class _Backend:
    """Stand-in for the voice manager the guards resolve via ``_voice_manager``."""

    def __init__(self, channel=None, error=None):
        self._channel = channel
        self._error = error
        self.calls = []

    def _get_server_channel(self, channel_id):
        if self._error is not None:
            raise self._error
        return self._channel

    def _require_permission(self, user_id, server_id, permission, channel_id=None):
        self.calls.append((user_id, server_id, permission, channel_id))


class _Guards(ConnectionManagementMixin, ScreenShareMixin):
    def __init__(self, backend):
        self._backend = backend

    def _voice_manager(self):
        return self._backend


def test_connect_guard_fails_closed_when_channel_lookup_raises():
    backend = _Backend(error=RuntimeError("database gone"))
    guards = _Guards(backend)

    with pytest.raises(PermissionDeniedError) as excinfo:
        guards._require_connect_permission(100, 4242)

    assert excinfo.value.permission == "voice.connect"
    # The guard never reached the permission check -- that is the point: a
    # failed lookup must deny outright, not fall through to "allowed".
    assert backend.calls == []


def test_stream_guard_fails_closed_when_channel_lookup_raises():
    backend = _Backend(error=RuntimeError("database gone"))
    guards = _Guards(backend)

    with pytest.raises(PermissionDeniedError) as excinfo:
        guards._require_stream_permission(100, 4242)

    assert excinfo.value.permission == "voice.speak"
    assert backend.calls == []


@pytest.mark.parametrize(
    "method,permission",
    [
        ("_require_connect_permission", "voice.connect"),
        ("_require_stream_permission", "voice.speak"),
    ],
)
def test_guards_still_enforce_permission_on_a_real_channel(method, permission):
    """The hardening must not weaken the normal, successful-lookup path."""
    backend = _Backend(channel={"server_id": 777})
    guards = _Guards(backend)

    getattr(guards, method)(100, 4242)

    assert backend.calls == [(100, 777, permission, 4242)]


@pytest.mark.parametrize(
    "method", ["_require_connect_permission", "_require_stream_permission"]
)
def test_guards_still_defer_when_channel_is_absent(method):
    """DM voice channels have no server row: preserve the caller-defer path."""
    backend = _Backend(channel=None)
    guards = _Guards(backend)

    getattr(guards, method)(100, 4242)  # must not raise

    assert backend.calls == []
