"""Follow-up to the 128-bit invite-code floor: downstream length assumptions.

``generate_invite_code()`` now mints ``secrets.token_urlsafe(16)`` codes: 22
characters drawn from ``[A-Za-z0-9_-]``. The automod invite-link rule still
extracted candidates with a fixed ``[a-zA-Z0-9]{8}`` pattern and only consulted
its invite-URL detector as a *filter* over those candidates. A freshly minted
22-char code therefore reached neither the "confirmed invite" nor the
"invite-shaped URL" branch: posting a live invite link in chat was silently
let through by moderation.

These tests mint a real code from the generator, prove it is a valid
join/invite code, and require the invite-link rule to detect it in every form a
user would actually paste.
"""

from __future__ import annotations

import pytest

from src.core.automod.models import ActionType, Rule, RuleAction, RuleType
from src.core.automod.rules.links import InviteLinkRule
from src.core.plexijoin.validation import validate_join_code
from src.core.servers.handlers.members.invites import MemberInviteMixin

pytestmark = [pytest.mark.security, pytest.mark.unit]

_URLSAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"  # pragma: allowlist secret
)


class _NeverCollidingDB:
    """Minimal DB for ``generate_invite_code``: no code ever collides."""

    def fetch_one(self, sql, params):
        return None


class _ConfiguredManager:
    def __init__(self, invite_code_length=8):
        self._config = {"invite_code_length": invite_code_length}


def _fresh_invite_code(invite_code_length=8) -> str:
    handler = MemberInviteMixin()
    handler.manager = _ConfiguredManager(invite_code_length)
    handler.db = _NeverCollidingDB()
    return handler.generate_invite_code()


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="invite length downstream test",
        rule_type=RuleType.INVITE_LINKS,
        enabled=True,
        config=config,
        actions=[RuleAction(action_type=ActionType.DELETE_MESSAGE)],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=0,
    )


def test_fresh_invite_code_is_a_valid_22_char_join_code():
    """Control: the minted code really is the new valid credential shape."""
    code = _fresh_invite_code()
    assert len(code) == 22
    assert set(code) <= _URLSAFE
    assert validate_join_code(code) == code


@pytest.mark.parametrize(
    "template",
    [
        "{code}",
        "join discord.gg/{code} now",
        "here: https://discord.gg/{code}",
        "https://chat.example/invite/{code}",
    ],
)
def test_fresh_22_char_invite_code_is_detected_in_chat(template):
    """A live 22-char invite must not evade the invite-link rule."""
    code = _fresh_invite_code()
    rule = InviteLinkRule(_rule({"block_all": True}))
    match = rule.check(
        template.format(code=code), 1, 1, {"known_invite_codes": [code]}
    )
    assert match.matched, f"fresh invite code was not detected in {template!r}"
    assert code in match.match_details["invite_codes"]


def test_fresh_invite_code_detected_in_allowlist_mode():
    """Allowlist mode still blocks non-allowlisted real invites."""
    code = _fresh_invite_code()
    rule = InviteLinkRule(_rule({"block_all": False}))
    match = rule.check(f"discord.gg/{code}", 1, 1, {"known_invite_codes": [code]})
    assert match.matched
    assert code in match.match_details["invite_codes"]


def test_allowlisted_fresh_invite_code_is_not_blocked():
    """Broadening candidate extraction must not block allowlisted codes."""
    code = _fresh_invite_code()
    rule = InviteLinkRule(_rule({"block_all": True, "allowed_codes": [code]}))
    match = rule.check(
        f"discord.gg/{code}", 1, 1, {"known_invite_codes": [code]}
    )
    assert not match.matched


def test_detector_is_not_vacuous():
    """Old 8-char codes still work; ordinary prose is not mass-blocked."""
    rule = InviteLinkRule(_rule({"block_all": True}))
    old_code = "Abc12345"
    assert validate_join_code(old_code) == old_code
    assert rule.check(
        f"join discord.gg/{old_code}", 1, 1, {"known_invite_codes": [old_code]}
    ).matched
    assert not rule.check(
        "just a normal message about servers and channels",
        1,
        1,
        {"known_invite_codes": ["Abc12345"]},
    ).matched
