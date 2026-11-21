"""Regression: the exported LifecycleMixin verifier must fail closed on a
missing/stored-empty IP/UA binding index.

``LifecycleMixin._verify_session_token`` still carried the fail-open form of the
session binding check: the presented blind index was compared only when the
stored ``ip_index``/``ua_index`` happened to be truthy. A session row minted
without a recorded IP/UA (or a legacy/corrupt row) therefore skipped the
comparison entirely, so a stolen bearer token could be replayed from any
address. The live composed AuthManager currently resolves verification to
VerificationMixin, but LifecycleMixin is an exported mixin (and is pinned by
``test_session_package.py``); its copy must enforce the same fail-closed
contract so a direct/MRO-future use cannot reintroduce the bypass.

These tests pin the fail-closed behaviour while proving a correctly bound
session still verifies and a mismatched context is still rejected (the checks
are hardened, not weakened).
"""

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.managers.session import lifecycle as lifecycle_mod
from src.core.auth.managers.session.lifecycle import LifecycleMixin

pytestmark = [pytest.mark.security, pytest.mark.unit]

IP = "203.0.113.10"
UA = "legit-browser/1.0"
OTHER_IP = "198.51.100.77"
OTHER_UA = "attacker/9.9"
_NOW = 1_700_000_000_000


class _FakeCrypto:
    """Deterministic stand-in for the blind-index primitive."""

    @staticmethod
    def blind_index(value: str, scope: str) -> str:
        return f"{scope}:{value}"

    @staticmethod
    def fast_blind_index(value: str, scope: str) -> str:
        return f"{scope}:{value}"


class _FakeDB:
    def __init__(self, row):
        self.row = row

    def fetch_one(self, *args, **kwargs):
        return self.row

    def execute(self, *args, **kwargs):
        class _Result:
            rowcount = 1

        return _Result()


class _LifecycleVerifier(LifecycleMixin):
    """Minimal host for the mixin method under test."""

    def __init__(self, row):
        self._db = _FakeDB(row)
        self.crypto = _FakeCrypto()
        self._config = {
            "security": {"token_binding": True},
            # Avoid the activity/expiry write path; it is not under test.
            "sessions": {"extend_on_activity": False},
        }

    def _get_timestamp(self) -> int:
        return _NOW

    # LifecycleMixin inherits only a protocol stub for this; the composed
    # AuthManager supplies base.py's implementation. Mirror it here.
    def _ua_index(self, ua):
        if not ua:
            return None
        return self.crypto.blind_index(ua, "user_agent")


def _row(ip_index, ua_index):
    return {
        "id": 1,
        "user_id": 7,
        "token_hash": "stored-hash",
        "revoked": 0,
        "deletion_status": "active",
        "expires_at": _NOW + 3_600_000,
        "ip_index": ip_index,
        "ua_index": ua_index,
        "device_id": None,
        "last_activity": _NOW,
        "permissions": "{}",
        "account_type": "user",
        "username": "victim",
        "account_locked": 0,
        "force_username_change": 0,
        "rate_limit_tier": "standard",
    }


@pytest.fixture(autouse=True)
def _accept_token_secret(monkeypatch):
    # The binding gate runs before code verification; stub the token hash check
    # so the test isolates the binding comparison.
    monkeypatch.setattr(lifecycle_mod, "verify_token_hash", lambda secret, stored: True)


def _verify(row, ip=IP, ua=UA):
    parsed = {"token_type": "session", "id": 1, "secret": "s"}
    return _LifecycleVerifier(row)._verify_session_token(
        parsed, ip_address=ip, user_agent=ua
    )


@pytest.mark.parametrize(
    "ip_index,ua_index",
    [
        (None, None),
        ("", ""),
        (None, "user_agent:" + UA),
        ("ip_address:" + IP, None),
        (123, "user_agent:" + UA),
        ("ip_address:" + IP, 456),
    ],
)
def test_missing_or_malformed_stored_index_fails_closed(ip_index, ua_index):
    # Precondition: without the fix these rows skip the binding comparison and
    # return a valid TokenInfo, i.e. a stolen token usable from any address.
    with pytest.raises(TokenInvalidError):
        _verify(_row(ip_index, ua_index))


def test_bound_session_still_verifies_from_same_context():
    info = _verify(_row("ip_address:" + IP, "user_agent:" + UA), ip=IP, ua=UA)
    assert info.valid is True
    assert info.session_id == 1


def test_bound_session_rejected_from_other_context():
    with pytest.raises(TokenInvalidError):
        _verify(
            _row("ip_address:" + IP, "user_agent:" + UA),
            ip=OTHER_IP,
            ua=UA,
        )
    with pytest.raises(TokenInvalidError):
        _verify(
            _row("ip_address:" + IP, "user_agent:" + UA),
            ip=IP,
            ua=OTHER_UA,
        )
