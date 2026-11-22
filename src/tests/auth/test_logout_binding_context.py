"""Regression (N644 follow-up): logout must re-check the presented token's
IP/User-Agent binding before revoking a session.

``LifecycleMixin.logout`` verifies only the token secret, then revokes the
session. With ``security.token_binding`` enabled a stolen session bearer is
unusable for *authenticated* requests from another host (``verify_token``
re-checks its blind indexes), but it could still be replayed into ``logout``
from any host to revoke the victim's session -- the same stale-context
availability DoS that ``refresh_session`` was hardened against in ded43f8e.

The token-consuming write must therefore re-check the presented context
against the session's stored blind indexes, constant-time and fail-closed,
before the revoke. Missing/corrupt binding material fails closed too.
"""

from unittest.mock import patch

import pytest

from src.core.auth import tokens
from src.core.auth.managers.session import lifecycle as lifecycle_mod
from src.core.auth.managers.session.lifecycle import LifecycleMixin

pytestmark = [pytest.mark.security, pytest.mark.unit]

IP = "203.0.113.10"
UA = "legit-browser/1.0"
OTHER_IP = "198.51.100.77"
OTHER_UA = "attacker/9.9"
_NOW = 1_700_000_000_000
SID = 42


class _FakeCrypto:
    @staticmethod
    def blind_index(value: str, scope: str) -> str:
        return f"{scope}:{value.lower().strip()}"

    @staticmethod
    def fast_blind_index(value: str, scope: str) -> str:
        return f"{scope}:{value.lower().strip()}"


class _FakeDB:
    def __init__(self, row):
        self.row = dict(row)
        self.revoke_writes = 0

    def fetch_one(self, sql, params=None):
        if "FROM auth_sessions WHERE id = ?" in sql:
            return dict(self.row)
        return None

    def execute(self, sql, params=None):
        if "UPDATE auth_sessions SET revoked = 1" in sql:
            self.revoke_writes += 1
            self.row["revoked"] = 1

        class _Result:
            rowcount = 1

        return _Result()


class _LogoutHost(LifecycleMixin):
    """Minimal host exercising only ``logout``."""

    def __init__(self, row, binding=True):
        self._db = _FakeDB(row)
        self.crypto = _FakeCrypto()
        self._config = {"security": {"token_binding": binding}}
        self.audits = []

    def _get_timestamp(self):
        return _NOW

    def _ua_index(self, ua):
        if not ua:
            return None
        return self.crypto.blind_index(ua, "user_agent")

    def _log_audit(self, *args, **kwargs):
        self.audits.append((args, kwargs))


def _row(ip_index, ua_index, token_secret):
    return {
        "id": SID,
        "user_id": 7,
        "token_hash": tokens.hash_token(token_secret),
        "revoked": 0,
        "ip_index": ip_index,
        "ua_index": ua_index,
    }


def _bound_row(secret):
    return _row("ip_address:" + IP, "user_agent:" + UA, secret)


@pytest.fixture(autouse=True)
def _isolate_side_effects(monkeypatch):
    monkeypatch.setattr(lifecycle_mod, "invalidate_pattern", lambda *_a, **_k: None)
    monkeypatch.setattr(
        lifecycle_mod, "close_session_gateway_connections", lambda *_a, **_k: None
    )


@pytest.mark.parametrize(
    "presented_ip,presented_ua",
    [
        (OTHER_IP, UA),
        (IP, OTHER_UA),
        (OTHER_IP, OTHER_UA),
    ],
)
def test_stolen_context_logout_is_denied_without_revoking(presented_ip, presented_ua):
    """A bound bearer replayed from a different context cannot log the victim out."""
    secret = tokens.generate_token_secret()
    token = f"{SID}.{secret}"
    host = _LogoutHost(_bound_row(secret))

    assert host.logout(token, presented_ip, presented_ua) is False
    assert host._db.revoke_writes == 0, "stale-context logout must not revoke"
    assert host.audits == [], "no logout audit for a denied attempt"
    # The real owner can still log out from the bound context.
    assert host.logout(token, IP, UA) is True
    assert host._db.revoke_writes == 1


@pytest.mark.parametrize(
    "presented_ip,presented_ua",
    [(None, UA), (IP, None), (None, None)],
)
def test_missing_context_fails_closed_when_binding_enabled(presented_ip, presented_ua):
    secret = tokens.generate_token_secret()
    token = f"{SID}.{secret}"
    host = _LogoutHost(_bound_row(secret))

    assert host.logout(token, presented_ip, presented_ua) is False
    assert host._db.revoke_writes == 0


@pytest.mark.parametrize(
    "ip_index,ua_index",
    [
        (None, None),
        ("", ""),
        (None, "user_agent:" + UA),
        ("ip_address:" + IP, None),
        (123, "user_agent:" + UA),
    ],
)
def test_missing_or_malformed_stored_binding_fails_closed(ip_index, ua_index):
    secret = tokens.generate_token_secret()
    token = f"{SID}.{secret}"
    host = _LogoutHost(_row(ip_index, ua_index, secret))

    assert host.logout(token, IP, UA) is False
    assert host._db.revoke_writes == 0


def test_same_context_logout_succeeds():
    secret = tokens.generate_token_secret()
    token = f"{SID}.{secret}"
    host = _LogoutHost(_bound_row(secret))

    assert host.logout(token, IP, UA) is True
    assert host._db.revoke_writes == 1
    assert host._db.row["revoked"] == 1
    assert len(host.audits) == 1


def test_binding_disabled_keeps_legacy_behavior():
    secret = tokens.generate_token_secret()
    token = f"{SID}.{secret}"
    host = _LogoutHost(_row(None, None, secret), binding=False)

    assert host.logout(token) is True
    assert host._db.revoke_writes == 1


def test_wrong_secret_still_fails():
    secret = tokens.generate_token_secret()
    host = _LogoutHost(_bound_row(secret))

    forged = f"{SID}.{tokens.generate_token_secret()}"
    assert host.logout(forged, IP, UA) is False
    assert host._db.revoke_writes == 0


def test_module_wrapper_threads_ip_and_ua():
    from src.core.auth import sessions as sessions_mod

    with patch.object(sessions_mod, "_get_auth_manager") as get_mgr:
        instance = get_mgr.return_value.get_instance.return_value
        instance.logout.return_value = True
        result = sessions_mod.logout("a.b", IP, UA)

    assert result is True
    instance.logout.assert_called_once_with("a.b", IP, UA)
