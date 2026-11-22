"""Regression: a backslash in the URL authority must not evade link filtering.

WHATWG URL parsers (browsers and http(s) clients) read a literal ``\\`` in a
special URL as an authority/path separator. ``https://evil.example\\@trusted.example/``
therefore opens ``evil.example`` -- the host on the LEFT of the backslash, with
``@trusted.example`` demoted to path data. Python's ``urlsplit`` and
``ExternalLinkRule._extract_host`` instead kept the backslash inside the
authority and derived the host from the text to the RIGHT of ``@``
(``trusted.example``).

That parser differential made both modes fail open: a denylisted host was not
blocked in blacklist mode, and a non-allowlisted host was accepted in whitelist
mode, even though the browser opened the blocked/unlisted host. The fix
canonicalizes backslashes to slashes *before* authority parsing so the filter
decides on the same host the consumer opens. Percent-encoded ``%5C`` and path
backslashes are data, not authority separators, and stay untouched.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from src.core.automod.models import (
    ActionType,
    Rule,
    RuleAction,
    RuleType,
)
from src.core.automod.rules.links import ExternalLinkRule

pytestmark = [pytest.mark.security, pytest.mark.unit]


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="backslash authority test",
        rule_type=RuleType.EXTERNAL_LINKS,
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
    return ExternalLinkRule(_rule({"mode": "blacklist", "blacklist": ["evil.example"]}))


def _whitelist_rule():
    return ExternalLinkRule(
        _rule({"mode": "whitelist", "whitelist": ["trusted.example"]})
    )


# (payload, host a WHATWG consumer opens). Every payload used to be reported as
# ``trusted.example`` (or nothing at all) by the old extractor, so the real host
# was never the one the moderation decision was made on.
_BACKSLASH_AUTHORITY = [
    (r"https://evil.example\@trusted.example/x", "evil.example"),
    (r"https://evil.example\@trusted.example", "evil.example"),
    (r"https://sub.evil.example\@trusted.example/x", "sub.evil.example"),
    (r"https:\evil.example/x", "evil.example"),
    (r"https:\\evil.example/x", "evil.example"),
    (r"//evil.example\@trusted.example/x", "evil.example"),
]


@pytest.mark.parametrize("payload, browser_host", _BACKSLASH_AUTHORITY)
def test_extract_host_follows_the_consumer_not_urlsplit(payload, browser_host):
    # Document the differential: the stdlib parser is NOT authoritative here.
    parsed = urlsplit(payload)
    assert parsed.hostname != browser_host

    # The moderation decision must use the host the browser actually opens.
    assert _blacklist_rule()._extract_host(payload) == browser_host


@pytest.mark.parametrize("payload, browser_host", _BACKSLASH_AUTHORITY)
def test_blacklist_no_longer_fails_open(payload, browser_host):
    match = _blacklist_rule().check(payload, 1, 1)
    assert match.matched, f"denylisted host evaded the filter: {payload!r}"
    assert browser_host in match.match_details["blocked_domains"]


@pytest.mark.parametrize("payload, browser_host", _BACKSLASH_AUTHORITY)
def test_whitelist_no_longer_fails_open(payload, browser_host):
    match = _whitelist_rule().check(payload, 1, 1)
    assert match.matched, f"non-allowlisted host passed the filter: {payload!r}"
    assert browser_host in match.match_details["blocked_domains"]


@pytest.mark.parametrize(
    "payload",
    [
        r"https://trusted.example/a\b",  # backslash in the path is data
        r"https://trusted.example/a%5Cb",  # percent-encoded backslash is data
        r"https://trusted.example/a\evil.example",  # path, not authority
        "https://trusted.example/x",  # ordinary allowlisted link
    ],
)
def test_backslashes_outside_authority_are_not_overblocked(payload):
    assert not _whitelist_rule().check(payload, 1, 1).matched
    assert not _blacklist_rule().check(payload, 1, 1).matched


def test_plain_denylisted_url_still_blocked():
    assert _blacklist_rule().check("visit https://evil.example/x", 1, 1).matched
