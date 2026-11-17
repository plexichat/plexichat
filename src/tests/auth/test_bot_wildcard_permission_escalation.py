"""Regression: a bot must never be granted the full ``*`` wildcard.

``validate_permissions(..., is_bot=True)`` checks bot-restricted scopes against
dotted wildcard *prefixes*. The all-permissions wildcard ``"*"`` matches no
prefix, so it used to sail through validation and give a bot every restricted
scope (``admin.system``, ``account.delete``, ``bots.create``, ...). The manager
path ``update_bot_permissions`` relies on this validator as its only gate, so
the flaw was a direct privilege-escalation for any bot owner.
"""

import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import AuthError
from src.core.auth.permissions import BOT_RESTRICTED_PERMISSIONS, validate_permissions


def test_bot_full_wildcard_is_rejected():
    valid, issues = validate_permissions({"*": True}, is_bot=True)
    assert valid is False
    assert issues
    blob = " ".join(issues).lower()
    for restricted in BOT_RESTRICTED_PERMISSIONS:
        assert restricted in blob


def test_denied_full_wildcard_is_harmless():
    # A wildcard explicitly set to False grants nothing, so it stays valid.
    assert validate_permissions({"*": False}, is_bot=True)[0] is True


def test_existing_dotted_wildcards_still_rejected():
    for wildcard in ("admin.*", "bots.*", "account.*"):
        valid, issues = validate_permissions({wildcard: True}, is_bot=True)
        assert valid is False
        assert any("cannot have" in issue.lower() for issue in issues)


def test_human_full_wildcard_still_allowed():
    assert validate_permissions({"*": True}, is_bot=False)[0] is True


def test_bot_owner_cannot_escalate_via_update_bot_permissions(db, auth_manager):
    """The manager's only gate must refuse a ``{"*": True}`` permission write."""
    from src.utils import encryption

    name = f"botwild_{uuid.uuid4().hex[:10]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        owner = auth_manager.register(name, f"{name}@example.com", "TestPass123!")

    bot = auth_manager.create_bot(
        owner_id=owner.id, username=f"{name}_bot", display_name="Bot"
    )
    with pytest.raises(AuthError, match="Bots cannot have permission"):
        auth_manager.update_bot_permissions(owner.id, bot.id, {"*": True})

    # The stored bot permissions are unchanged after the refused write.
    row = db.fetch_one("SELECT permissions FROM auth_bots WHERE id = ?", (bot.id,))
    assert row is not None
    assert "*" not in (row["permissions"] or "")
