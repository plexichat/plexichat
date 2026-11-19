"""Regression: provisional-license host reach and non-finite license limits.

Two license-boundary weaknesses are locked down here:

1. A provisional (``type: "test"``) license is unsigned, so its
   ``allowed_hosts`` is attacker-editable data. Trusting a non-loopback entry
   let anyone forge a test license -- which enables every feature and passes
   the admin host check -- and serve it from a public address, defeating the
   only guard keeping provisional licenses out of production. Host lists on
   test licenses are now clamped to loopback.

2. Python's ``json`` accepts ``NaN``/``Infinity``. ``int(inf)`` raises
   ``OverflowError``, which the limit-enforcement callers did not catch, so a
   crafted license could crash registration/upload/server-creation with a 500.
   Non-finite limits now map to the "unlimited" sentinel instead of raising.
"""

import time

import pytest

import utils.licensing as licensing
from utils.licensing.core import (
    DEFAULT_TEST_ALLOWED_HOSTS,
    LicenseManager,
)

pytestmark = [pytest.mark.security, pytest.mark.unit]

_PUBLIC_IP = "203.0.113.5"


def _test_license(**overrides):
    base = {
        "version": "1.0",
        "type": "test",
        "instance_id": "forged-selftest",
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + 86400,
        "features": {"all": True},
        "limits": {},
        "allowed_hosts": list(DEFAULT_TEST_ALLOWED_HOSTS),
        "signature": None,
    }
    base.update(overrides)
    return base


def test_forged_test_license_cannot_allow_non_loopback_host():
    """A self-declared public allowed_host must not widen a test license."""
    mgr = LicenseManager()
    mgr.load_from_dict(_test_license(allowed_hosts=[_PUBLIC_IP]))

    assert mgr.validate().is_valid is True  # still a valid provisional license
    assert _PUBLIC_IP not in mgr.get_allowed_hosts()
    assert all(host in DEFAULT_TEST_ALLOWED_HOSTS for host in mgr.get_allowed_hosts())
    assert mgr.is_valid_for_host(_PUBLIC_IP) is False
    assert mgr.is_valid_for_host("127.0.0.1") is True


def test_mixed_test_license_host_list_keeps_only_loopback():
    """Loopback entries are still honoured; only non-loopback ones are dropped."""
    mgr = LicenseManager()
    mgr.load_from_dict(_test_license(allowed_hosts=["127.0.0.1", _PUBLIC_IP, "::1"]))

    assert mgr.get_allowed_hosts() == ["127.0.0.1", "::1"]
    assert mgr.is_valid_for_host(_PUBLIC_IP) is False
    assert mgr.is_valid_for_host("::1") is True


def test_standard_license_allowed_hosts_are_not_tampered_with():
    """The clamp applies only to provisional licenses, not signed ones."""
    mgr = LicenseManager()
    mgr.load_from_dict(
        {
            "version": "1.0",
            "type": "standard",
            "instance_id": "prod",
            "issued_at": int(time.time()),
            "expires_at": None,
            "features": {"bond": True},
            "limits": {},
            "allowed_hosts": [_PUBLIC_IP],
            "signature": "unused-for-this-host-only-check",
        }
    )
    assert mgr.get_allowed_hosts() == [_PUBLIC_IP]
    assert mgr.is_valid_for_host(_PUBLIC_IP) is True


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_limit_is_not_enforced_and_does_not_crash(value):
    assert licensing.is_limit_enforced(value) is False


@pytest.mark.parametrize("value", [-1, None, 0, 50, "100"])
def test_enforceable_limit_sentinel_semantics_unchanged(value):
    assert licensing.is_limit_enforced(value) == (value is not None and int(value) >= 0)


def test_get_license_limit_non_finite_returns_default(monkeypatch):
    """A non-finite limit from a crafted license cannot crash enforcement."""

    class _FakeManager:
        def get_feature_limit(self, name, default):
            assert name == "max_users"
            return float("inf")

    monkeypatch.setattr(licensing, "_setup_called", True, raising=False)
    monkeypatch.setattr(licensing, "_free_tier_mode", False, raising=False)
    monkeypatch.setattr(licensing, "_license_manager", _FakeManager(), raising=False)

    assert licensing.get_license_limit("max_users", default=-1) == -1
