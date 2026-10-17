"""M8: is_ip_blocked must fail OPEN on DB/cache errors.

- Listed IP stays blocked (normal path returns True).
- DB exception returns False (not blocked) with a logged warning.
- Unlisted IP returns False.
"""

from unittest.mock import MagicMock, patch

from src.core.auth.managers.ip_blacklist import IpBlacklistMixin


def _make_mixin(fetch_one_result=None, fetch_one_effect=None):
    mixin = object.__new__(IpBlacklistMixin)
    mixin._db = MagicMock()
    if fetch_one_effect is not None:
        mixin._db.fetch_one.side_effect = fetch_one_effect
    else:
        mixin._db.fetch_one.return_value = fetch_one_result
    mixin.crypto = MagicMock()
    mixin.crypto.fast_blind_index.return_value = "idx"
    mixin.crypto.legacy_fast_blind_index.return_value = "legacy"
    mixin._get_timestamp = MagicMock(return_value=1_000_000)
    mixin.unblock_ip = MagicMock(return_value=True)
    return mixin


def _call(mixin, ip):
    # Bypass the @cached tier to test the fail-open logic directly.
    return IpBlacklistMixin.is_ip_blocked.__wrapped__(mixin, ip)


def test_blocked_ip_stays_blocked():
    mixin = _make_mixin(fetch_one_result={"expires_at": None})
    assert _call(mixin, "203.0.113.9") is True


def test_db_exception_fails_open_not_blocked():
    mixin = _make_mixin(fetch_one_effect=RuntimeError("db down"))
    with patch("src.core.auth.managers.ip_blacklist.logger") as log:
        result = _call(mixin, "203.0.113.10")
    assert result is False
    assert log.warning.called


def test_unlisted_ip_not_blocked():
    mixin = _make_mixin(fetch_one_result=None)
    assert _call(mixin, "198.51.100.7") is False
