"""Regression (N644): refresh rotation must re-check the *presented* token's
IP/User-Agent binding before consuming it.

The rotation path used to verify only the token secret, then atomically consume
the old bearer. ``verify_token`` on the freshly minted token happened *after*
rotation in the HTTP route, so a stolen refresh token used from another host
could still force a rotation: the atomic consume retired the victim's current
credential and the victim's next refresh tripped replay-based revocation, even
though the attacker could not use the new token from that host. With
``security.token_binding`` enabled the rotation must therefore re-check the
presented request context against the session's stored blind indexes
constant-time and fail closed, before consuming anything.
"""

from unittest.mock import patch

import pytest

from src.core.auth import tokens
from src.core.auth.managers.session import lifecycle as lifecycle_mod
from src.core.auth.managers.session.lifecycle import LifecycleMixin
from src.core.auth.replay import BoundedReplayGuard

pytestmark = [pytest.mark.security, pytest.mark.unit]

IP = "203.0.113.10"
UA = "legit-browser/1.0"
OTHER_IP = "198.51.100.77"
OTHER_UA = "attacker/9.9"
_NOW = 1_700_000_000_000
SID = 42


class _FakeCrypto:
    """Deterministic blind-index stand-in (same primitive for both verifier and rotation)."""

    @staticmethod
    def blind_index(value: str, scope: str) -> str:
        return f"{scope}:{value.lower().strip()}"

    @staticmethod
    def fast_blind_index(value: str, scope: str) -> str:
        return f"{scope}:{value.lower().strip()}"


class _FakeDB:
    def __init__(self, row):
        self.row = dict(row)
        self.rotate_writes = 0

    def fetch_one(self, sql, params=None):
        if "FROM auth_sessions WHERE id = ?" in sql:
            return dict(self.row)
        return None

    def execute(self, sql, params=None):
        if "UPDATE auth_sessions SET token_hash" in sql:
            self.rotate_writes += 1
            self.row["token_hash"] = params[0]
            self.row["expires_at"] = params[1]
            self.row["last_activity"] = params[2]

        class _Result:
            rowcount = 1

        return _Result()


class _RefreshHost(LifecycleMixin):
    """Minimal host exercising only ``refresh_session``."""

    def __init__(self, row, binding=True):
        self._db = _FakeDB(row)
        self.crypto = _FakeCrypto()
        self._config = {
            "security": {"token_binding": binding},
            "sessions": {},
        }
        self.guard = BoundedReplayGuard(max_entries=64, ttl_seconds=60)
        self.replay_revoked = []

    def _get_timestamp(self):
        return _NOW

    def _ua_index(self, ua):
        if not ua:
            return None
        return self.crypto.blind_index(ua, "user_agent")

    def _session_expire_hours(self, user_id, device_id, sessions_config):
        return 720

    def _revoke_for_refresh_replay(self, session_id, row):
        self.replay_revoked.append(session_id)
        self._db.row["revoked"] = 1


def _row(ip_index, ua_index, token_secret):
    return {
        "id": SID,
        "user_id": 7,
        "token_hash": tokens.hash_token(token_secret),
        "revoked": 0,
        "expires_at": _NOW + 3_600_000,
        "ip_index": ip_index,
        "ua_index": ua_index,
        "device_id": None,
        "last_activity": _NOW,
    }


def _bound_row(secret):
    return _row("ip_address:" + IP, "user_agent:" + UA, secret)


def _rotated_key(secret):
    return f"refresh:{SID}:{tokens.hash_token(secret)}"


@pytest.fixture(autouse=True)
def _isolate_side_effects(monkeypatch):
    # Avoid cache/gateway coupling; the guard is per-host and fresh each test.
    monkeypatch.setattr(lifecycle_mod, "invalidate_pattern", lambda *_a, **_k: None)
    monkeypatch.setattr(
        lifecycle_mod, "close_session_gateway_connections", lambda *_a, **_k: None
    )


def _make_host(row, binding=True):
    host = _RefreshHost(row, binding=binding)
    return host, patch.object(lifecycle_mod, "refresh_replay_guard", host.guard)


def test_same_context_refresh_succeeds_and_consumes_old_token():
    secret = tokens.generate_token_secret()
    token = f"{SID}.{secret}"
    host, guard_ctx = _make_host(_bound_row(secret))

    with guard_ctx:
        new_token = host.refresh_session(token, IP, UA)

    assert new_token is not None and new_token != token
    assert host._db.rotate_writes == 1
    assert host.guard.contains(_rotated_key(secret)), "old token must be consumed"
    assert host.replay_revoked == [], "a legit rotation is not a replay"


@pytest.mark.parametrize(
    "presented_ip,presented_ua",
    [
        (OTHER_IP, UA),  # other host
        (IP, OTHER_UA),  # other user agent
        (OTHER_IP, OTHER_UA),
    ],
)
def test_stolen_context_refresh_is_denied_without_consuming_old_token(
    presented_ip, presented_ua
):
    """Deny before consume: the victim's credential and session stay intact."""
    secret = tokens.generate_token_secret()
    token = f"{SID}.{secret}"
    host, guard_ctx = _make_host(_bound_row(secret))

    with guard_ctx:
        assert host.refresh_session(token, presented_ip, presented_ua) is None
        # No state changed: no rotation, no consume, no replay revocation.
        assert host._db.rotate_writes == 0
        assert not host.guard.contains(_rotated_key(secret))
        assert host.replay_revoked == []
        # The real owner can still rotate from the bound context.
        assert host.refresh_session(token, IP, UA) is not None
        assert host._db.rotate_writes == 1


@pytest.mark.parametrize(
    "presented_ip,presented_ua",
    [(None, UA), (IP, None), (None, None)],
)
def test_missing_context_fails_closed_when_binding_enabled(presented_ip, presented_ua):
    secret = tokens.generate_token_secret()
    token = f"{SID}.{secret}"
    host, guard_ctx = _make_host(_bound_row(secret))

    with guard_ctx:
        assert host.refresh_session(token, presented_ip, presented_ua) is None
        assert host._db.rotate_writes == 0


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
    host, guard_ctx = _make_host(_row(ip_index, ua_index, secret))

    with guard_ctx:
        assert host.refresh_session(token, IP, UA) is None
        assert host._db.rotate_writes == 0


def test_binding_disabled_keeps_legacy_behavior():
    """With binding off (default), rotation needs no context -- no regression."""
    secret = tokens.generate_token_secret()
    token = f"{SID}.{secret}"
    host, guard_ctx = _make_host(_row(None, None, secret), binding=False)

    with guard_ctx:
        assert host.refresh_session(token) is not None
        assert host._db.rotate_writes == 1


def test_module_wrapper_threads_ip_and_ua():
    """The public wrapper must forward context to the manager (route contract)."""
    from src.core.auth import sessions as sessions_mod

    sentinel = object()
    with patch.object(sessions_mod, "_get_auth_manager") as get_mgr:
        instance = get_mgr.return_value.get_instance.return_value
        instance.refresh_session.return_value = "new.token"
        result = sessions_mod.refresh_session("old.token", IP, UA)

    assert result == "new.token"
    instance.refresh_session.assert_called_once_with("old.token", IP, UA)
