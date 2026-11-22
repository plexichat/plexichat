"""A bot token must never carry scopes its OWNER no longer holds.

The delegation rule is enforced when a bot's permission map is written:
``create_bot`` and ``update_bot_permissions`` both reject a grant the owner
does not hold.  It is *not* enforced when the bot credential is used.  Bot
tokens carry ``auth_bots.permissions`` verbatim into every ``TokenInfo``
(``_verify_bot_token``), so a permission that was legitimately delegated while
the owner held it survives a later demotion: the admin strips the scope from
the human account, but the bot keeps exercising it through its own token.

This module proves the gap end to end (create -> delegate -> demote -> verify)
and pins the fail-closed ceiling at verification: the effective permission
map of a bot token is intersected with the owner's CURRENT grants, and a
missing/unreadable owner row denies the bot entirely.
"""

import inspect
import json
import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.permissions import has_permission
from src.core.auth.managers.session.verification import VerificationMixin


def _make_admin_owner_and_bot(db, auth_manager, suffix="ceil"):
    """Register an owner with a full wildcard and delegate a bot scope."""
    from src.utils import encryption

    name = f"botceil_{suffix}_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        owner = auth_manager.register(name, f"{name}@example.com", "TestPass123!")
    db.execute(
        "UPDATE auth_users SET permissions = ? WHERE id = ?",
        (json.dumps({"*": True, "bots.create": True, "bots.manage": True}), owner.id),
    )
    bot = auth_manager.create_bot(
        owner_id=owner.id,
        username=f"{name}_bot",
        display_name="Delegated Bot",
        # A scope the owner currently holds only through the "*" wildcard.
        permissions={"moderation.ban": True},
    )
    return owner, bot


def _demote_owner(db, owner_id, retained=None):
    """Strip the owner's wildcard exactly as an admin demotion would.

    ``retained`` names scopes the demoted owner still holds, so the control
    case can prove the ceiling clips only lost scopes.
    """
    perms = {"bots.create": True, "bots.manage": True}
    for scope in retained or ():
        perms[scope] = True
    db.execute(
        "UPDATE auth_users SET permissions = ? WHERE id = ?",
        (json.dumps(perms), owner_id),
    )
    from src.core.database import invalidate_pattern

    invalidate_pattern("token_verify:*")


def test_bot_token_loses_delegated_scope_when_owner_is_demoted(db, auth_manager):
    owner, bot = _make_admin_owner_and_bot(db, auth_manager)

    before = auth_manager.verify_token(bot.token)
    assert has_permission(before.permissions, "moderation.ban")
    assert has_permission({"*": True}, "moderation.ban")  # delegation was valid

    _demote_owner(db, owner.id)
    # The demoted owner can no longer ban, and the ceiling must follow through
    # to the delegated credential.
    owner_row = db.fetch_one("SELECT permissions FROM auth_users WHERE id = ?", (owner.id,))
    assert not has_permission(json.loads(owner_row["permissions"]), "moderation.ban")

    after = auth_manager.verify_token(bot.token)
    assert not has_permission(after.permissions, "moderation.ban"), (
        "bot token kept a moderation scope after the owner was demoted"
    )


def test_bot_token_keeps_scopes_owner_still_holds(db, auth_manager):
    """Control: an undelegated demotion must not strip the bot's other scopes."""
    owner, bot = _make_admin_owner_and_bot(db, auth_manager, "hold")
    auth_manager.update_bot_permissions(
        owner.id, bot.id, {"moderation.ban": True, "messages.send": True}
    )
    # The demotion keeps messages.send but drops moderation.ban.
    _demote_owner(db, owner.id, retained=("messages.send",))

    after = auth_manager.verify_token(bot.token)
    # messages.send is still held by the demoted owner's default grants, so it
    # remains usable; only the lost moderation scope is clipped.
    assert has_permission(after.permissions, "messages.send")


def test_bot_token_denied_when_owner_row_missing(db, auth_manager):
    """A dangling owner reference must not let the bot authenticate at all."""
    owner, bot = _make_admin_owner_and_bot(db, auth_manager, "ghost")
    ghost_id = owner.id + 1_000_000_000
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("UPDATE auth_bots SET owner_id = ? WHERE id = ?", (ghost_id, bot.id))
    finally:
        db.execute("PRAGMA foreign_keys = ON")
    from src.core.database import invalidate_pattern

    invalidate_pattern("token_verify:*")

    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(bot.token)


def test_live_verifier_enforces_owner_ceiling_in_source():
    """Pin the live verifier so a refactor cannot silently drop the ceiling."""
    source = inspect.getsource(VerificationMixin._verify_bot_token)
    assert "SELECT permissions FROM auth_users" in source
    assert "has_permission(owner_perms, perm)" in source
    assert "if not owner_row" in source
    assert "permissions=effective_perms" in source
