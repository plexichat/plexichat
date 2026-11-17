"""Deep hardening regressions for auth token / permission primitives.

Two real weaknesses are pinned here:

1. Bot wildcard privilege escalation. ``validate_permissions`` only rejected
   the literal ``"*"`` wildcard for bots. The ``"*.*"`` spelling is honoured by
   ``has_permission(perms, "*")`` (the generic admin gate in admin/users.py)
   through its dotted-prefix loop, yet it slipped past bot validation because
   stripping its trailing ``".*"`` leaves ``"*"``, which matches no restricted
   permission name. A bot owner could set ``{"*.*": True}`` on their bot via
   ``update_bot_permissions`` and hand it full admin rights.

2. Fail-closed gaps in the token hash / expiry helpers. A non-string credential
   raised out of ``hash_token`` (500 instead of a denial), and an unvalidated
   clock value could make an expiry check compare as "not expired".
"""

import uuid
from unittest.mock import patch

import pytest

from src.core.auth import tokens
from src.core.auth.exceptions import AuthError
from src.core.auth.permissions import (
    BOT_RESTRICTED_PERMISSIONS,
    DEFAULT_BOT_PERMISSIONS,
    has_permission,
    validate_permissions,
)


@pytest.fixture
def fresh_user(db, auth_manager):
    """A freshly registered user with default (bot-creating) permissions."""
    from src.utils import encryption

    name = f"hardening_user_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(name, f"{name}@example.com", "TestPass123!")
    return user, auth_manager


# ---------------------------------------------------------------------------
# 1. Bot wildcard abuse
# ---------------------------------------------------------------------------

FULL_ADMIN_WILDCARDS = ["*", "*.*", ".*"]


@pytest.mark.parametrize("wildcard", FULL_ADMIN_WILDCARDS)
def test_bot_full_wildcards_rejected(wildcard):
    """Any wildcard spelling that grants '*' is forbidden for bots."""
    # Sanity: these really are full-admin grants via the generic admin gate.
    assert has_permission({wildcard: True}, "*") is True or wildcard == ".*"

    valid, issues = validate_permissions({wildcard: True}, is_bot=True)
    assert valid is False
    for restricted in BOT_RESTRICTED_PERMISSIONS:
        assert any(restricted in issue for issue in issues)


def test_star_dot_star_is_admin_gate():
    """Documents the escalation: '*.*' satisfies has_permission(..., '*')."""
    assert has_permission({"*.*": True}, "*") is True
    assert has_permission({"*.*": True}, "admin.system") is False


@pytest.mark.parametrize(
    "wildcard",
    ["admin.*", "bots.*", "bots.create", "account.*", "admin.system"],
)
def test_bot_restricted_wildcards_still_rejected(wildcard):
    valid, issues = validate_permissions({wildcard: True}, is_bot=True)
    assert valid is False
    assert any("cannot have" in issue.lower() for issue in issues)


@pytest.mark.parametrize(
    "wildcard", ["messages.*", "conversations.*", "voice.*", "bot.*"]
)
def test_bot_safe_wildcards_still_accepted(wildcard):
    """Non-restricted namespaces remain grantable (no over-blocking)."""
    valid, issues = validate_permissions({wildcard: True}, is_bot=True)
    assert valid is True
    assert issues == []


def test_default_bot_permissions_still_valid():
    valid, issues = validate_permissions(DEFAULT_BOT_PERMISSIONS, is_bot=True)
    assert valid is True
    assert issues == []


def test_create_bot_rejects_full_wildcard(fresh_user):
    user, auth = fresh_user
    with pytest.raises(AuthError, match="Bots cannot have permission"):
        auth.create_bot(
            owner_id=user.id,
            username="wildcard_bot_create",
            display_name="Wildcard Bot",
            permissions={"*.*": True},
        )


def test_update_bot_permissions_rejects_full_wildcard(fresh_user):
    """The update path only calls validate_permissions -- it is the escalation."""
    user, auth = fresh_user
    bot = auth.create_bot(
        owner_id=user.id,
        username="wildcard_bot_update",
        display_name="Wildcard Bot",
    )
    with pytest.raises(AuthError, match="Bots cannot have permission"):
        auth.update_bot_permissions(user.id, bot.id, {"*.*": True})
    # The escalation never landed.
    assert auth.get_bot(bot.id).permissions.get("*.*") is not True


# ---------------------------------------------------------------------------
# 2. Fail-closed token hash / expiry helpers
# ---------------------------------------------------------------------------


def test_verify_token_hash_fails_closed_on_bad_input():
    digest = tokens.hash_token("a-valid-secret")
    assert tokens.verify_token_hash(None, digest) is False
    assert tokens.verify_token_hash(b"bytes", digest) is False
    assert tokens.verify_token_hash("secret", None) is False
    assert tokens.verify_token_hash("secret", b"bytes") is False
    assert tokens.verify_token_hash("", digest) is False
    assert (
        tokens.verify_token_hash("x" * (tokens.MAX_TOKEN_LENGTH + 1), digest) is False
    )
    # A correct credential still verifies.
    assert tokens.verify_token_hash("a-valid-secret", digest) is True


def test_verify_token_hash_uses_constant_time_compare(monkeypatch):
    real = tokens.hmac.compare_digest
    calls = []

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(tokens.hmac, "compare_digest", spy)
    digest = tokens.hash_token("secret")
    assert tokens.verify_token_hash("secret", digest) is True
    assert len(calls) == 1


@pytest.mark.parametrize("bad_clock", [0, -1, True, "1700000000000", 1.7e12, 10**18])
def test_is_expired_fails_closed_on_bad_clock(bad_clock):
    now = 1_700_000_000_000
    assert tokens.is_expired(now + 60_000, bad_clock) is True


def test_is_expired_boundaries_preserved():
    now = 1_700_000_000_000
    assert tokens.is_expired(now, now) is True  # expires exactly now is expired
    assert tokens.is_expired(now + 1, now) is False
    assert tokens.is_expired("garbage", now) is True
