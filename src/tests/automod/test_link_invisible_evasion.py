"""Regression: invisible Unicode separators must not defeat link filters.

A single visually undetectable format character (U+200B zero-width space,
U+FEFF BOM, bidi overrides, ...) used to make the external-domain and invite
code detectors fail open:

* inside a URL the host extractor saw a non-ASCII token and returned ``None``;
* between prose and a bare domain the tokenizer never split the glued token.

Both link rules now scan two canonical views of the message (invisibles
deleted, and invisibles replaced with word boundaries) before matching, just
like the keyword/duplicate-spam layers already do.
"""

import pytest

from src.core.automod.models import (
    Rule,
    RuleAction,
    ActionType,
    RuleType,
)
from src.core.automod.rules.links import ExternalLinkRule, InviteLinkRule


def _make_rule(rule_type, config):
    return Rule(
        id=1,
        server_id=1,
        name="t",
        rule_type=rule_type,
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


def _blacklist_rule():
    return ExternalLinkRule(
        _make_rule(
            RuleType.EXTERNAL_LINKS,
            {"mode": "blacklist", "blacklist": ["evil.com"]},
        )
    )


def _whitelist_rule():
    return ExternalLinkRule(
        _make_rule(
            RuleType.EXTERNAL_LINKS,
            {"mode": "whitelist", "whitelist": ["example.com"]},
        )
    )


def _invite_rule():
    return InviteLinkRule(_make_rule(RuleType.INVITE_LINKS, {"block_all": True}))


@pytest.mark.parametrize(
    "text",
    [
        "visit evil.com/x today",  # control: plain form was already blocked
        "visit\u200bevil.com/x today",  # separator between prose and domain
        "\u200bhttps://evil.com/x today",  # separator before the scheme
        "ht\u200btps://evil.com/x",  # separator inside the scheme
        "https://evil\u200b.com/x",  # separator inside the host
        "https://evil.com\u200b/x",  # separator after the host
        "https://evil.com/x\u200btoday",  # separator after the path
        "\ufeffhttps://evil.com/x",  # BOM prefix
        "https://\u202eevil.com/x",  # bidi override before the host
        "https://\uff45vil.com/x",  # compatibility (fullwidth) form
    ],
)
def test_blacklist_catches_invisibly_split_links(text):
    match = _blacklist_rule().check(text, 1, 1)
    assert match.matched, text
    assert match.match_details["blocked_domains"] == ["evil.com"], text


@pytest.mark.parametrize(
    "text",
    [
        "see evil.com/x",
        "see \u200bevil.com/x",
        "see evil\u200b.com/x",
        "see\ufeffevil.com/x",
    ],
)
def test_whitelist_blocks_non_allowlisted_domain_despite_invisibles(text):
    match = _whitelist_rule().check(text, 1, 1)
    assert match.matched, text
    # The delete-view can also surface a fused token (``seeevil.com`` when a
    # BOM glues "see" to the domain). Whitelist mode is fail-closed, so the
    # important property is that the real link is among the blocked hosts.
    assert "evil.com" in match.match_details["blocked_domains"], text


def test_whitelisted_domain_with_invisible_before_scheme_is_allowed():
    rule = _whitelist_rule()
    assert not rule.check("see \u200bhttps://example.com/a", 1, 1).matched
    assert not rule.check("see \ufeffexample.com/a", 1, 1).matched


def test_whitelist_fails_closed_for_invisible_inside_domain():
    # Splitting the allowlisted domain into two tokens yields a host that is
    # not on the allowlist; fail closed rather than silently allow it.
    match = _whitelist_rule().check("see exa\u200bmple.com/a", 1, 1)
    assert match.matched
    assert "mple.com" in match.match_details["blocked_domains"]


@pytest.mark.parametrize(
    "text",
    [
        "join discord.gg/abc12345",  # control
        "join discord.gg/\u200babc12345",  # separator before the code
        "join \u200bdiscord.gg/abc12345",  # separator before the URL
        "join discord\u200b.gg/abc12345",  # separator inside the host
        "join d\u200biscord.gg/abc12345",  # separator inside the label
    ],
)
def test_invite_filter_catches_invisibly_split_invites(text):
    assert _invite_rule().check(text, 1, 1).matched, text


def test_plain_text_without_links_is_not_flagged():
    assert not _blacklist_rule().check("nothing to see here", 1, 1).matched
    assert not _invite_rule().check("nothing to see here", 1, 1).matched
