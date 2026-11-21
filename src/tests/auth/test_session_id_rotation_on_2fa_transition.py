"""Session-id fixation across the 2FA privilege transition.

A password-only session minted before a user enables (or disables) 2FA is a
credential an attacker may already know. ``confirm_2fa`` / ``disable_2fa`` used
by the HTTP routes preserve the *current* session id via ``except_session_id``
so the user is not logged out -- but that leaves the pre-transition id valid
after the second factor is switched on/off. The fixate-an-id proof below shows
the fixed id is still accepted after the step-up; the route tests pin that both
transitions now rotate the id and re-issue the HttpOnly cookie.
"""

import asyncio
import inspect
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pyotp
import pytest

from src.core.auth.exceptions import TokenInvalidError


def _sid(token: str) -> int:
    return int(token.split(".", 1)[0])


def _register_and_login(auth, username):
    from src.utils import encryption

    with patch.object(encryption, "verify_password", return_value=True):
        user = auth.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
        login = auth.login(username, "TestPass123!")
    return user, login


class TestFixedIdSurvivesStepUp:
    """Pre-fix state, captured to prove the gap is real (not vacuous).

    The manager still preserves a supplied ``except_session_id`` (that contract
    is used by non-HTTP callers); it is the HTTP privilege-transition route that
    must rotate it. This test documents the raw gap the route fix closes.
    """

    def test_fixed_pre2fa_id_accepted_after_enabling_2fa(self, db, auth_manager):
        user, login = _register_and_login(auth_manager, "fixate_pre2fa_id")
        fixed_sid = _sid(login.token)
        fixed_token = login.token
        secret = auth_manager.setup_2fa(user.id).secret

        # This is exactly what the route did before the fix: keep the current
        # session id alive across the privilege transition.
        assert (
            auth_manager.confirm_2fa(
                user.id, pyotp.TOTP(secret).now(), except_session_id=fixed_sid
            )
            is True
        )

        # The attacker-known pre-2FA token still authenticates: the session id
        # was never regenerated, so fixation survives the "authentication".
        info = auth_manager.verify_token(fixed_token)
        assert info.valid is True
        assert info.session_id == fixed_sid


class _RecordingWS:
    def __init__(self):
        self.closed = []

    async def close(self, code=None, reason=None):
        self.closed.append((code, reason))
        return True


def _fake_conn(user_id, auth_session_id, name):
    conn = SimpleNamespace(
        user_id=int(user_id),
        auth_session_id=int(auth_session_id),
        connection_id=name,
        websocket=_RecordingWS(),
        disconnecting=False,
        disconnected=False,
    )

    async def _flush():
        return None

    conn.flush_outbound = _flush
    conn.set_disconnecting = lambda: setattr(conn, "disconnecting", True)
    conn.set_disconnected = lambda: setattr(conn, "disconnected", True)
    return conn


def _install_dispatcher(monkeypatch, conns):
    from src.api.websocket.dispatcher import GatewayDispatcher
    import src.api.websocket as ws

    class _FakeSessionManager:
        def get_all_connections(self):
            return list(conns)

    dispatcher = GatewayDispatcher(_FakeSessionManager())
    monkeypatch.setattr(ws, "is_setup", lambda: True)
    monkeypatch.setattr(ws, "get_dispatcher", lambda: dispatcher)
    return dispatcher


class TestSessionIdRotation:
    def test_rotation_closes_only_old_socket_and_new_session_stays_valid(
        self, monkeypatch, db, auth_manager
    ):
        """Rotation closes the OLD session's socket, not a sibling's, and the
        freshly minted session remains a valid credential.

        This is the conflict check: ``rotate_session_id`` revokes via
        :meth:`revoke_session`, whose targeted close must use the OLD id. The
        new id is minted after the close, so no socket can be bound to it yet.
        """
        from src.api.websocket.dispatcher import GatewayCloseCode

        user, login = _register_and_login(auth_manager, "rotate_socket_target")
        old_sid = _sid(login.token)
        sibling = auth_manager.create_session_for_user(user.id)
        conn_old = _fake_conn(user.id, old_sid, "old")
        conn_sibling = _fake_conn(user.id, sibling.session.id, "sibling")
        _install_dispatcher(monkeypatch, [conn_old, conn_sibling])

        async def _main():
            rotated = auth_manager.rotate_session_id(user.id, old_sid)
            await asyncio.sleep(0.01)
            return rotated

        rotated = asyncio.run(_main())

        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(login.token)
        assert auth_manager.verify_token(rotated.token).valid is True
        assert rotated.id not in (old_sid, sibling.session.id)

        assert conn_old.websocket.closed, "old session's socket must close"
        code, _reason = conn_old.websocket.closed[0]
        assert code == int(GatewayCloseCode.AUTHENTICATION_FAILED)
        assert not conn_sibling.websocket.closed, (
            "a sibling session's socket must stay connected"
        )

    def test_rotate_session_id_invalidates_old_and_keeps_user_bound(
        self, db, auth_manager
    ):
        user, login = _register_and_login(auth_manager, "rotate_pre2fa_id")
        old_sid = _sid(login.token)
        old_token = login.token
        secret = auth_manager.setup_2fa(user.id).secret
        auth_manager.confirm_2fa(
            user.id, pyotp.TOTP(secret).now(), except_session_id=old_sid
        )

        # Pre-condition: the pre-transition (attacker-known) id still works.
        assert auth_manager.verify_token(old_token).valid is True

        rotated = auth_manager.rotate_session_id(user.id, old_sid)
        assert rotated.id != old_sid
        assert _sid(rotated.token) == rotated.id

        # The fixed id is now dead; the legitimate user gets a fresh, valid one.
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(old_token)
        info = auth_manager.verify_token(rotated.token)
        assert info.valid is True
        assert info.session_id == rotated.id
        assert info.user_id == user.id
        # Legitimate session properties (device binding) are preserved.
        assert rotated.device_id == login.session.device_id

    def test_rotate_session_id_rejects_foreign_session(self, db, auth_manager):
        user, _login = _register_and_login(auth_manager, "rotate_owner_id")
        _other, other_login = _register_and_login(auth_manager, "rotate_foreign_id")
        with pytest.raises(TokenInvalidError):
            auth_manager.rotate_session_id(user.id, _sid(other_login.token))

    @staticmethod
    def _call(method, **kwargs):
        result = method(**kwargs)
        if inspect.isawaitable(result):
            return asyncio.run(result)
        return result

    def test_confirm_2fa_route_rotates_cookie(self):
        from fastapi import Response

        from src.api.routes.auth.two_factor import confirm_2fa_setup

        fake = MagicMock()
        fake.confirm_2fa.return_value = True
        rotated = SimpleNamespace(
            id=999,
            token="999.freshsecretvalue",
            expires_at=int(time.time() * 1000) + 3_600_000,
        )
        fake.rotate_session_id.return_value = rotated

        response = Response()
        with patch(
            "src.api.routes.auth.two_factor.api.get_auth", return_value=fake
        ):
            self._call(
                confirm_2fa_setup,
                body=SimpleNamespace(code="123456"),
                response=response,
                current_user=SimpleNamespace(user_id=1, session_id=111),
            )

        fake.confirm_2fa.assert_called_once_with(1, "123456", except_session_id=111)
        fake.rotate_session_id.assert_called_once_with(1, 111)
        cookie = response.headers.get("set-cookie", "")
        assert "plexichat_token=999.freshsecretvalue" in cookie

    def test_disable_2fa_route_rotates_cookie(self):
        from fastapi import Response

        from src.api.routes.auth.two_factor import disable_2fa

        fake = MagicMock()
        fake.disable_2fa.return_value = True
        rotated = SimpleNamespace(
            id=1000,
            token="1000.freshsecretvalue",
            expires_at=int(time.time() * 1000) + 3_600_000,
        )
        fake.rotate_session_id.return_value = rotated

        response = Response()
        with patch(
            "src.api.routes.auth.two_factor.api.get_auth", return_value=fake
        ):
            self._call(
                disable_2fa,
                body=SimpleNamespace(password="TestPass123!", code="123456"),
                response=response,
                current_user=SimpleNamespace(user_id=1, session_id=222),
            )

        fake.disable_2fa.assert_called_once_with(
            1, "TestPass123!", "123456", except_session_id=222
        )
        fake.rotate_session_id.assert_called_once_with(1, 222)
        cookie = response.headers.get("set-cookie", "")
        assert "plexichat_token=1000.freshsecretvalue" in cookie
