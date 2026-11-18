"""Regression: an empty admin host allowlist must fail closed.

``check_host_restriction`` used to return True for an empty ``allowed_hosts``
list, which silently turned the admin host gate into an allow-all rule. An
operator disables that gate explicitly with
``admin_ui.host_restriction.enabled = false``; an empty/missing list is a
misconfiguration and must deny every client, not expose the admin surface.
"""

import pytest

from src.core.admin import check_host_restriction


def test_empty_allowlist_denies_network_peer():
    assert check_host_restriction("203.0.113.10", []) is False


@pytest.mark.parametrize("loopback", ["127.0.0.1", "localhost", "::1"])
def test_empty_allowlist_denies_loopback_too(loopback):
    assert check_host_restriction(loopback, []) is False


def test_missing_allowlist_denies_all():
    assert check_host_restriction("203.0.113.10", None) is False


def test_explicit_allowlist_still_enforced():
    assert check_host_restriction("203.0.113.10", ["203.0.113.0/24"]) is True
    assert check_host_restriction("198.51.100.5", ["203.0.113.0/24"]) is False
    assert check_host_restriction("127.0.0.1", ["127.0.0.1"]) is True
