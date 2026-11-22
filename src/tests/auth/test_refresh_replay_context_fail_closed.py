"""N720 follow-up: refresh-token *replay* revocation must re-check the
presented IP/User-Agent binding before killing the session.

``refresh_session`` already re-checks the presented credential's context before
it consumes/rotates a *current* token (N644).  But the replay branch that runs
first -- ``refresh_replay_guard.contains(rotated_key)`` ->
``_revoke_for_refresh_replay`` -- sat *ahead* of that context check.  A bearer
that is cryptographically valid (it is a real, previously issued token) but
presented from the wrong host/user-agent could therefore revoke the victim's
session without ever passing the binding gate, the same stale-context
availability DoS N720 closed in ``logout``.

A rotated token is a leaked token, so replay from the *bound* context must
still revoke (leak detection is preserved).  What must not happen is a
wrong-context (or unbound) bearer driving that revoke.
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


class _ReplayHost(LifecycleMixin):
    def __init__(self, row, binding=True):
        self._db = _FakeDB(row)
        self.crypto = _FakeCrypto()
        self._config = {"security": {"token_binding": binding}, "sessions": {}}
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
        # Record that the revoke was driven, and mirror its state change.
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
    monkeypatch.setattr(lifecycle_mod, "invalidate_pattern", lambda *_a, **_k: None)
    monkeypatch.setattr(
        lifecycle_mod, "close_session_gateway_connections", lambda *_a, **_k: None
    )


def _stale_token(host, secret):
    """Simulate a token the victim already rotated: its key is in the guard and
    the live row now carries a different secret (the victim's current token)."""
    host.guard.add(_rotated_key(secret))
    new_secret = tokens.generate_token_secret()
    host._db.row["token_hash"] = tokens.hash_token(new_secret)
    return f"{SID}.{secret}"


def _make_host(row, binding=True):
    host = _ReplayHost(row, binding=binding)
    return host, patch.object(lifecycle_mod, "refresh_replay_guard", host.guard)


@pytest.mark.parametrize(
    "presented_ip,presented_ua",
    [
        (OTHER_IP, UA),
        (IP, OTHER_UA),
        (OTHER_IP, OTHER_UA),
        (None, UA),
        (IP, None),
    ],
)
def test_wrong_context_replay_cannot_revoke(presented_ip, presented_ua):
    """A stale-context bearer holding an already-rotated token must be denied
    before the replay-revocation branch, leaving the session intact."""
    secret = tokens.generate_token_secret()
    host, guard_ctx = _make_host(_bound_row(secret))
    token = _stale_token(host, secret)

    with guard_ctx:
        assert host.refresh_session(token, presented_ip, presented_ua) is None

    assert host.replay_revoked == [], "stale-context replay must not revoke"
    assert host._db.row["revoked"] == 0, "victim session must stay live"
    assert host._db.rotate_writes == 0


@pytest.mark.parametrize(
    "ip_index,ua_index",
    [(None, None), ("", ""), (None, "user_agent:" + UA), ("ip_address:" + IP, None)],
)
def test_missing_stored_binding_replay_cannot_revoke(ip_index, ua_index):
    secret = tokens.generate_token_secret()
    host, guard_ctx = _make_host(_row(ip_index, ua_index, secret))
    token = _stale_token(host, secret)

    with guard_ctx:
        assert host.refresh_session(token, IP, UA) is None

    assert host.replay_revoked == []
    assert host._db.row["revoked"] == 0


def test_bound_context_replay_still_revokes():
    """Leak detection is preserved: a rotated token replayed from the *bound*
    context still revokes the compromised session."""
    secret = tokens.generate_token_secret()
    host, guard_ctx = _make_host(_bound_row(secret))
    token = _stale_token(host, secret)

    with guard_ctx:
        assert host.refresh_session(token, IP, UA) is None

    assert host.replay_revoked == [SID]
    assert host._db.row["revoked"] == 1


def test_binding_disabled_replay_still_revokes():
    """No regression when token_binding is off (legacy behaviour)."""
    secret = tokens.generate_token_secret()
    host, guard_ctx = _make_host(_row(None, None, secret), binding=False)
    token = _stale_token(host, secret)

    with guard_ctx:
        assert host.refresh_session(token, IP, UA) is None

    assert host.replay_revoked == [SID]
