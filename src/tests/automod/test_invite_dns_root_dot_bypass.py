"""Regression: DNS-root-dot invite hosts must not evade the invite-shape gate.

``ExternalLinkRule`` strips a trailing DNS-root dot when comparing hosts, but
``InviteLinkRule._INVITE_SHAPE_RE`` matched the bare ``discord.gg/<code>``
short form with a literal ``discord\\.gg/``. A user could therefore paste
``https://discord.gg./<code>`` -- which resolves to exactly the same host --
and the code reached neither the confirmed nor the invite-shaped branch, so a
live invite link was published unmoderated. (The ``<host>/invite/<code>``
branch already tolerated the dot, making the short form the odd one out.)

The fix widens only the short-form literal by one optional trailing dot. It
does not turn a *different* host such as ``discord.gg.evil.example`` into an
invite, so ordinary links are not mass-blocked.
"""

from __future__ import annotations

import pytest

from src.core.automod.models import ActionType, Rule, RuleAction, RuleType
from src.core.automod.rules.links import InviteLinkRule

pytestmark = [pytest.mark.security, pytest.mark.unit]


def _rule(config=None):
    return Rule(
        id=1,
        server_id=1,
        name="invite dns-root-dot test",
        rule_type=RuleType.INVITE_LINKS,
        enabled=True,
        config=config or {"block_all": True},
        actions=[RuleAction(action_type=ActionType.DELETE_MESSAGE)],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=0,
    )


def _check(content, config=None):
    return InviteLinkRule(_rule(config)).check(content, 1, 1, {})


@pytest.mark.parametrize(
    "code",
    [
        "Abc12345",  # legacy 8-char code still works
        "aBcD_efGhIjKlMnOpQrStUv",  # fresh 22-char urlsafe (token_urlsafe(16))
    ],
)
@pytest.mark.parametrize(
    "template",
    [
        "https://discord.gg./{code}",
        "discord.gg./{code}",
        "https://DISCORD.GG./{code}",
        "join https://discord.gg./{code} now",
    ],
)
def test_dns_root_dot_discord_short_form_is_detected(template, code):
    content = template.format(code=code)
    match = _check(content)
    assert match.matched, f"DNS-root-dot invite host evaded moderation: {content!r}"
    assert code in match.match_details["invite_codes"]


def test_discord_com_invite_with_root_dot_is_control():
    """The /invite/ branch already tolerated the dot; it must keep working."""
    code = "aBcD_efGhIjKlMnOpQrStUv"
    match = _check(f"https://discord.com./invite/{code}")
    assert match.matched
    assert code in match.match_details["invite_codes"]


@pytest.mark.parametrize(
    "content",
    [
        # A lookalike HOST must not become an invite just because it starts
        # with the discord.gg label -- no mass-block / false positives.
        "https://discord.gg.evil.example/aBcD_efGhIjKlMnOpQrStUv",
        "https://discord.gg-app.example/aBcD_efGhIjKlMnOpQrStUv",
        # Ordinary prose, a random 22-char string, a base64 blob and a message
        # id must all stay clean (the urlsafe candidate regex is indicator-only).
        "just a normal message about servers and channels",
        "reference aBcD_efGhIjKlMnOpQrStUv end",
        "token Q29tcGxldGVseSBub3IgYW4gaW52aXRl",
        "msg id 3cd1ba825d6e06a859e031d6f1696819",
    ],
)
def test_no_false_positives_from_widened_short_form(content):
    assert not _check(content).matched
