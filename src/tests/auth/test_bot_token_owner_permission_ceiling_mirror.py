"""Regression: the LifecycleMixin mirror of bot-token verification must apply
the owner-permission (delegation revocation) ceiling.

The composed AuthManager resolves ``_verify_bot_token`` to VerificationMixin,
whose copy intersects a bot's stored permission map with its owner's *current*
grants so a bot cannot keep exercising a scope after the owner is demoted.
LifecycleMixin is an exported mixin whose copy is reachable by direct unbound
call (and by any MRO reorder); it still returned ``auth_bots.permissions``
verbatim. A demoted owner's bot — or a bot with a dangling owner row — could
therefore authenticate with stale scopes through that copy.

These tests pin the fail-closed ceiling on the mirror while proving a still-held
delegation is preserved and the ordinary token checks are not weakened.
"""

import json

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.managers.session import lifecycle as lifecycle_mod
from src.core.auth.managers.session.lifecycle import LifecycleMixin
from src.core.auth.permissions import has_permission

pytestmark = [pytest.mark.security, pytest.mark.unit]

BOT_ID = 777
OWNER_ID = 4242
IP = "203.0.113.10"
PARSED = {"token_type": "bot", "id": BOT_ID, "secret": "presented-secret"}


class _BotDB:
    """Minimal DB stub distinguishing the bot and owner SELECTs."""

    def __init__(self, bot_row, owner_row):
        self.bot_row = bot_row
        self.owner_row = owner_row
        self.owner_queries = 0

    def fetch_one(self, sql, params=()):
        if "auth_bots" in sql:
            return self.bot_row
        if "auth_users" in sql:
            self.owner_queries += 1
            return self.owner_row
        raise AssertionError(f"unexpected SQL: {sql}")


class _Host(LifecycleMixin):
    """Bare host for the unbound mixin method under test."""

    def __init__(self, db):
        self._db = db


def _bot_row(perms):
    return {
        "id": BOT_ID,
        "owner_id": OWNER_ID,
        "username": "delegated-bot",
        "token_hash": "stored-hash",
        "disabled": 0,
        "permissions": json.dumps(perms),
    }


def _verify(db):
    # Direct unbound call selects LifecycleMixin's copy regardless of MRO.
    return LifecycleMixin._verify_bot_token(_Host(db), PARSED, IP)


@pytest.fixture(autouse=True)
def _accept_token_secret(monkeypatch):
    # Isolate the delegation ceiling from the token-hash comparison. Individual
    # tests may override this to prove the ordinary checks still run.
    monkeypatch.setattr(
        lifecycle_mod, "verify_token_hash", lambda secret, stored: True
    )


def test_mirror_clips_delegated_scope_after_owner_demotion():
    """A lost owner scope must not survive in the bot token's verdict."""
    db = _BotDB(
        _bot_row({"moderation.ban": True, "messages.send": True}),
        {"permissions": json.dumps({"bots.manage": True, "messages.send": True})},
    )
    info = _verify(db)
    assert not has_permission(info.permissions, "moderation.ban"), (
        "mirror let a bot keep a scope its owner was demoted from"
    )
    assert has_permission(info.permissions, "messages.send")
    assert db.owner_queries == 1, "mirror did not read the owner's current grants"


def test_mirror_denies_when_owner_row_missing():
    """A dangling owner reference must not let the bot authenticate."""
    db = _BotDB(_bot_row({"moderation.ban": True}), None)
    with pytest.raises(TokenInvalidError):
        _verify(db)


def test_mirror_denies_on_malformed_owner_permissions():
    """A non-map owner permission payload fails closed."""
    db = _BotDB(
        _bot_row({"moderation.ban": True}),
        {"permissions": json.dumps([1, 2, 3])},
    )
    with pytest.raises(TokenInvalidError):
        _verify(db)


def test_mirror_preserves_still_held_delegation():
    """Non-vacuous control: a scope the owner still holds stays usable."""
    db = _BotDB(
        _bot_row({"moderation.ban": True}),
        {"permissions": json.dumps({"*": True})},
    )
    info = _verify(db)
    assert has_permission(info.permissions, "moderation.ban")


def test_mirror_still_rejects_bad_secret(monkeypatch):
    """Non-vacuous control: the ordinary hash check is not weakened."""
    monkeypatch.setattr(
        lifecycle_mod, "verify_token_hash", lambda secret, stored: False
    )
    db = _BotDB(
        _bot_row({"moderation.ban": True}),
        {"permissions": json.dumps({"*": True})},
    )
    with pytest.raises(TokenInvalidError):
        _verify(db)
