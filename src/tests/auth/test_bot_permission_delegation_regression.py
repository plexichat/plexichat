"""Regression: a bot owner may only delegate permissions they themselves hold.

``update_bot_permissions`` validated only the *shape* of the permission map
(known scopes, no bot-only restrictions) and then wrote it verbatim to
``auth_bots.permissions``. Unlike ``create_bot``, it never checked that the
owner actually held the permissions being granted. Bot tokens carry the stored
``auth_bots.permissions`` verbatim into every ``TokenInfo``, so a user with the
default ``bots.create``/``bots.manage`` rights could escalate their bot to
moderation scopes (``moderation.ban``, ``messages.delete_others``,
``channels.manage``, ...) they cannot use themselves.
"""

import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import PermissionDeniedError


def _make_owner_and_bot(auth_manager, suffix="deleg"):
    from src.utils import encryption

    name = f"botdeleg_{suffix}_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        owner = auth_manager.register(name, f"{name}@example.com", "TestPass123!")
    bot = auth_manager.create_bot(
        owner_id=owner.id, username=f"{name}_bot", display_name="Bot"
    )
    return owner, bot


def _stored_permissions(db, bot_id):
    row = db.fetch_one("SELECT permissions FROM auth_bots WHERE id = ?", (bot_id,))
    assert row is not None
    return row["permissions"] or ""


def test_owner_cannot_grant_unheld_permission(db, auth_manager):
    """A default owner must not be able to escalate their bot's scopes."""
    owner, bot = _make_owner_and_bot(auth_manager)

    with pytest.raises(PermissionDeniedError, match="not held by owner"):
        auth_manager.update_bot_permissions(owner.id, bot.id, {"moderation.ban": True})

    assert "moderation.ban" not in _stored_permissions(db, bot.id)


def test_owner_cannot_grant_unheld_permission_through_mixed_map(db, auth_manager):
    """One unheld scope poisons the whole write; nothing is persisted."""
    owner, bot = _make_owner_and_bot(auth_manager, "mixed")

    with pytest.raises(PermissionDeniedError, match="not held by owner"):
        auth_manager.update_bot_permissions(
            owner.id,
            bot.id,
            {"messages.send": True, "channels.manage": True},
        )

    stored = _stored_permissions(db, bot.id)
    assert "channels.manage" not in stored


def test_owner_can_grant_held_permission(db, auth_manager):
    """Delegation of a scope the owner actually holds still works."""
    owner, bot = _make_owner_and_bot(auth_manager, "held")

    result = auth_manager.update_bot_permissions(
        owner.id, bot.id, {"messages.send": True}
    )
    assert result.permissions.get("messages.send") is True
    assert "messages.send" in _stored_permissions(db, bot.id)


def test_owner_wildcard_covers_granted_permission(db, auth_manager):
    """A namespace wildcard held by the owner covers a delegated scope."""
    from src.core.auth.permissions import permissions_to_json

    owner, bot = _make_owner_and_bot(auth_manager, "wildcard")
    db.execute(
        "UPDATE auth_users SET permissions = ? WHERE id = ?",
        (permissions_to_json({"messages.*": True, "bots.create": True}), owner.id),
    )

    auth_manager.update_bot_permissions(
        owner.id, bot.id, {"messages.delete_others": True}
    )
    assert "messages.delete_others" in _stored_permissions(db, bot.id)


def test_missing_owner_row_fails_closed(db, auth_manager):
    """A dangling owner reference must not bypass the delegation check."""
    owner, bot = _make_owner_and_bot(auth_manager, "ghost")
    ghost_id = owner.id + 1_000_000_000
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("UPDATE auth_bots SET owner_id = ? WHERE id = ?", (ghost_id, bot.id))
    finally:
        db.execute("PRAGMA foreign_keys = ON")

    with pytest.raises(PermissionDeniedError, match="Owner not found"):
        auth_manager.update_bot_permissions(ghost_id, bot.id, {"moderation.ban": True})

    assert "moderation.ban" not in _stored_permissions(db, bot.id)
