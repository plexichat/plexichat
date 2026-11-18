"""Regression: credential stuffing from a single source must be throttled.

The per-(user, IP) throttle and the distributed account lockout are both keyed
to an individual account. An attacker spraying one password across many
accounts therefore sees only a single failure per account and never trips
either control. The manager must additionally budget failures by source IP so
a single origin cannot grind through a password list across the user base.

The HTTP login route also has a per-IP rate limit, but it lives in the API
layer and is disabled when rate limiting is turned off; the manager-level
budget is the last line of defense for non-HTTP callers and such deployments.
"""

import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import InvalidCredentialsError
from src.utils import encryption


def _register(auth_manager, label="stuffing"):
    unique = uuid.uuid4().hex[:10]
    username = f"{label}_{unique}"
    auth_manager.register(
        username=username,
        email=f"{username}@example.com",
        password="TestPass123!",
    )
    return username


def test_single_ip_spray_is_throttled_across_accounts(db, auth_manager, monkeypatch):
    manager = auth_manager._get_manager()
    # Small budget keeps the regression fast; production default is much higher.
    monkeypatch.setitem(manager._config["security"], "credential_stuffing_ip_limit", 5)
    attacker_ip = "198.51.100.77"

    # Every attempt uses a DIFFERENT username with the SAME guessed password.
    # No single account accumulates failures, so the per-user throttle and the
    # multi-IP lockout never fire; only the per-IP budget can stop this.
    with patch.object(encryption, "verify_password", return_value=False):
        for _ in range(5):
            with pytest.raises(InvalidCredentialsError):
                auth_manager.login(
                    _register(auth_manager, "spray"),
                    "WrongPassword123!",
                    ip_address=attacker_ip,
                )

    # A sixth attempt from the same source is refused uniformly, even when it
    # is the correct password for a legitimate account.
    legit = _register(auth_manager, "legit")
    with (
        patch.object(encryption, "verify_password", return_value=True),
        pytest.raises(InvalidCredentialsError),
    ):
        auth_manager.login(legit, "TestPass123!", ip_address=attacker_ip)

    # A different source is completely unaffected.
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(legit, "TestPass123!", ip_address="198.51.100.78")
    assert result.status.value == "success"


def test_global_failure_rows_never_lock_or_touch_real_accounts(
    db, auth_manager, monkeypatch
):
    """The budget is source-keyed: it must not lock accounts or be cleared by a
    successful login, and its sentinel rows live apart from per-user rows."""
    manager = auth_manager._get_manager()
    monkeypatch.setitem(manager._config["security"], "credential_stuffing_ip_limit", 2)
    ip = "203.0.113.77"

    victim = _register(auth_manager, "victim")
    with patch.object(encryption, "verify_password", return_value=False):
        for _ in range(2):
            with pytest.raises(InvalidCredentialsError):
                auth_manager.login(victim, "WrongPassword123!", ip_address=ip)

    # The victim's account is untouched: the global budget never locks it.
    user = auth_manager.get_user_by_username(victim)
    assert user.account_locked is False

    # A successful login from a clean IP does not erase the source's budget.
    with patch.object(encryption, "verify_password", return_value=True):
        auth_manager.login(victim, "TestPass123!", ip_address="203.0.113.99")
    with (
        patch.object(encryption, "verify_password", return_value=True),
        pytest.raises(InvalidCredentialsError),
    ):
        auth_manager.login(victim, "TestPass123!", ip_address=ip)
