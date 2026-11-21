"""Registration ``max_users`` license gate must fail closed.

``RegistrationMixin.register`` enforces the licensed ``max_users`` ceiling.
Previously an uninitialised license manager or a missing licensing module
raised inside that gate and was swallowed by
``except (RuntimeError, ImportError): pass``, which dropped *all* user-count
enforcement and let a licensed instance register unlimited users -- the same
fail-open class as the server-creation gate, in a different limit.

These tests prove the gate still denies when the license cannot be evaluated,
and that the licensed/free-tier verdicts are unchanged.
"""

import sys
from unittest.mock import patch

import pytest

import utils.licensing as licensing
from src.core.auth.exceptions import AuthError

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _license_unavailable(monkeypatch):
    """Make ``get_license_limit`` raise RuntimeError mid-gate."""

    def _boom(*args, **kwargs):
        raise RuntimeError("licensing not configured")

    monkeypatch.setattr(licensing, "is_free_tier", lambda: False)
    monkeypatch.setattr(licensing, "get_license_limit", _boom)


def test_registration_denied_when_license_check_raises(auth_manager, monkeypatch):
    _license_unavailable(monkeypatch)

    with pytest.raises(AuthError) as exc:
        auth_manager.register("gateuser1", "gateuser1@example.com", "TestPass123!")

    assert exc.value.field == "user_limit"


def test_registration_denied_when_licensing_module_missing(auth_manager):
    # A None entry in sys.modules makes the import in the gate raise ImportError.
    with patch.dict(sys.modules, {"utils.licensing": None}):
        with pytest.raises(AuthError) as exc:
            auth_manager.register("gateuser2", "gateuser2@example.com", "TestPass123!")

    assert exc.value.field == "user_limit"


def test_finite_license_cap_is_still_enforced(auth_manager, monkeypatch):
    """A healthy, licensed gate keeps enforcing the license's own cap."""
    # Seed one user while free tier (no cap).
    auth_manager.register("capuser1", "capuser1@example.com", "TestPass123!")

    monkeypatch.setattr(licensing, "is_free_tier", lambda: False)
    monkeypatch.setattr(licensing, "get_license_limit", lambda *a, **k: 1)

    with pytest.raises(AuthError) as exc:
        auth_manager.register("capuser2", "capuser2@example.com", "TestPass123!")

    assert exc.value.field == "user_limit"


def test_free_tier_registration_unrestricted(auth_manager, monkeypatch):
    """Free tier keeps its prior unlimited-registration behaviour."""
    monkeypatch.setattr(licensing, "is_free_tier", lambda: True)

    user = auth_manager.register("freeuser1", "freeuser1@example.com", "TestPass123!")

    assert user is not None
