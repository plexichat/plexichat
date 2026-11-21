"""A configured webhook signing secret must meet the generated secret's bar.

``ApplicationManager._resolve_webhook_secret`` accepts an operator-supplied
``applications.webhook_signature_secret`` from config. Every generated secret is
full strength: the in-manager fallback is ``secrets.token_urlsafe(48)`` (64
urlsafe chars, 384 bits) and both the config default and the startup generator
use ``secrets.token_hex(32)`` (64 hex chars, 256 bits). The configured value,
however, was returned verbatim with NO length floor, so a one-character secret
could become the HMAC key for webhook delivery signatures - trivially
brute-forceable from a single captured delivery (256 candidates). That is the
same class of gap as the caller-supplied API-token and admin-password paths:
a credential-setting path that accepts a caller value without the strength
floor its generated sibling applies.

The manager must never *use* a configured secret below the floor; it rotates to
a freshly generated, persisted secret instead (same as the legacy literal).
"""

import pytest

import utils.config as config
from src.core.applications.manager.base import ApplicationManager

pytestmark = [pytest.mark.security, pytest.mark.applications]

# Match the codebase's credential floors (API token / federation shared key)
# and far below any of the generated webhook secrets.
EXPECTED_MIN = 32


def _resolve(monkeypatch, configured, persisted):
    monkeypatch.setattr(
        config, "set", lambda section, value: persisted.update(value or {})
    )
    return ApplicationManager._resolve_webhook_secret(
        {"webhook_signature_secret": configured}
    )


def test_weak_configured_webhook_secret_is_not_used(monkeypatch):
    """A short operator-supplied secret must never sign webhook deliveries."""
    persisted = {}
    resolved = _resolve(monkeypatch, "a", persisted)

    assert resolved != "a", "one-character secret was accepted as the signing key"
    assert len(resolved) >= EXPECTED_MIN
    assert persisted.get("webhook_signature_secret") == resolved, (
        "rotated secret must be persisted so workers agree on the signing key"
    )


def test_moderately_short_secret_is_also_rotated(monkeypatch):
    persisted = {}
    resolved = _resolve(monkeypatch, "short-secret-123", persisted)

    assert resolved != "short-secret-123"
    assert len(resolved) >= EXPECTED_MIN


def test_strong_configured_webhook_secret_is_kept(monkeypatch):
    """Control: a configured secret at the floor is not needlessly rotated."""
    strong = "S" * EXPECTED_MIN
    persisted = {}
    resolved = _resolve(monkeypatch, strong, persisted)

    assert resolved == strong
    assert persisted == {}, "a strong secret must not be rewritten"


def test_generated_secret_meets_floor(monkeypatch):
    """Control: the no-config path still mints a full-strength secret."""
    persisted = {}
    resolved = _resolve(monkeypatch, "", persisted)

    assert len(resolved) >= EXPECTED_MIN
    assert persisted.get("webhook_signature_secret") == resolved
