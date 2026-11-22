"""Regression: source-IP failure budgets key on the canonical address.

``auth_failed_logins`` indexes a source IP by a blind index of the *raw* string.
``blind_index`` only lowercases/strips, so equivalent IPv6 spellings of one
address (``2001:db8::1``, ``2001:0db8::1``, ``2001:db8:0:0:0:0:0:1``, ...) get
independent rows. A client whose address arrives via a trusted proxy's
X-Forwarded-For header controls that spelling, which breaks both sides of the
lockout design:

* an attacker rotates spellings to get a fresh per-IP and global credential-
  stuffing budget for every attempt; and
* a single source can inflate ``COUNT(DISTINCT ip_index)`` past
  ``lockout_min_distinct_ips`` and lock out an arbitrary account — the exact
  single-IP denial of service that setting was introduced to prevent.

Canonicalise the address before hashing it so every spelling of one IP shares
one budget and counts as one distinct source.
"""

import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import InvalidCredentialsError
from src.utils import encryption

pytestmark = [pytest.mark.auth]


def _register(auth_manager):
    username = f"ipcanon_{uuid.uuid4().hex[:10]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    return username


_SPELLINGS = (
    "2001:db8::1",
    "2001:0db8:0:0:0:0:0:1",
    "2001:db8:0:0:0::1",
    "2001:0db8::1",
)


def test_ipv6_spelling_rotation_cannot_evade_global_budget(
    db, auth_manager, monkeypatch
):
    manager = auth_manager._get_manager()
    monkeypatch.setitem(manager._config["security"], "credential_stuffing_ip_limit", 3)
    username = _register(auth_manager)

    # Three failures from ONE address, each spelled differently.
    with patch.object(encryption, "verify_password", return_value=False):
        for ip in _SPELLINGS[:3]:
            with pytest.raises(InvalidCredentialsError):
                auth_manager.login(username, "WrongPassword123!", ip_address=ip)

    # A fourth attempt from yet another spelling of the same address must be
    # refused even with the correct password: the budget is per source, not per
    # textual representation.
    with (
        patch.object(encryption, "verify_password", return_value=True),
        pytest.raises(InvalidCredentialsError),
    ):
        auth_manager.login(username, "TestPass123!", ip_address=_SPELLINGS[3])


def test_ipv6_spelling_rotation_cannot_fake_distinct_ips(db, auth_manager, monkeypatch):
    manager = auth_manager._get_manager()
    monkeypatch.setitem(manager._config["security"], "lockout_min_distinct_ips", 2)
    monkeypatch.setitem(manager._config["security"], "max_failed_attempts", 3)
    # Keep the global per-IP budget out of the way; this test targets lockout.
    monkeypatch.setitem(
        manager._config["security"], "credential_stuffing_ip_limit", 100
    )
    username = _register(auth_manager)

    with patch.object(encryption, "verify_password", return_value=False):
        for ip in _SPELLINGS:
            with pytest.raises(InvalidCredentialsError):
                auth_manager.login(username, "WrongPassword123!", ip_address=ip)

    user = auth_manager.get_user_by_username(username)
    assert user.account_locked is False
