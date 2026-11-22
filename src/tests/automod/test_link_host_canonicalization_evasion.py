"""Regression: automod's host decision must agree with the URL consumer.

Two parser differentials let a blocked / non-allowlisted host pass while the
browser opened it:

* Percent-encoded bytes in the host (``evil%2eexample`` -> ``evil.example``,
  ``%65vil.example`` -> ``evil.example``) were reported as "not a link at all"
  and skipped by both blacklist and whitelist modes.
* Internationalised hosts and punycode (``xn--...``) hosts never agreed with
  a policy entry written in the other form: a punycode denylist entry did not
  match a Unicode URL and vice versa, and a punycode TLD (``.xn--p1ai``) was
  rejected outright by the alphabetic-TLD guard.

The fix canonicalizes BOTH observed hosts and list entries to the same shape
(percent-decoded, IDNA/punycode, lowercased, DNS-root dots stripped) so the
moderation decision is made on the name the consumer resolves.
"""

from __future__ import annotations

import pytest

from src.core.automod.models import ActionType, Rule, RuleAction, RuleType
from src.core.automod.rules.links import ExternalLinkRule

pytestmark = [pytest.mark.security, pytest.mark.unit]


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="host canonicalization test",
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


def _blacklist(*entries):
    return ExternalLinkRule(
        _rule({"mode": "blacklist", "blacklist": list(entries)})
    )


def _whitelist(*entries):
    return ExternalLinkRule(
        _rule({"mode": "whitelist", "whitelist": list(entries)})
    )


# A URL whose authority encodes host bytes. WHATWG clients decode these before
# resolution, so the browser opens `evil.example` even though a naive token
# parser sees only `%xx` separators.
_ENCODED_HOST = [
    "http://evil%2eexample/x",
    "http://evil.example%2e/x",
    "http://%65vil.example/x",
    "http://user@evil%2eexample/x",
]


@pytest.mark.parametrize("payload", _ENCODED_HOST)
def test_percent_encoded_host_is_blocked(payload):
    rule = _blacklist("evil.example")
    assert rule._extract_host(payload) == "evil.example"
    match = rule.check(payload, 1, 1)
    assert match.matched, f"encoded denylisted host evaded the filter: {payload!r}"
    assert "evil.example" in match.match_details["blocked_domains"]


@pytest.mark.parametrize("payload", _ENCODED_HOST)
def test_percent_encoded_unlisted_host_fails_whitelist_closed(payload):
    match = _whitelist("trusted.example").check(payload, 1, 1)
    assert match.matched, f"unlisted encoded host passed the whitelist: {payload!r}"


# An IDN and its punycode form are the same host. Both list-entry directions
# must agree with both URL forms.
_IDN_HOSTS = [
    ("xn--vil-qdd.example", "еvil.example"),  # Cyrillic e + "vil.example"
]


@pytest.mark.parametrize("punycode, unicode_form", _IDN_HOSTS)
@pytest.mark.parametrize("entry", ["punycode", "unicode"])
@pytest.mark.parametrize("url_form", ["punycode", "unicode"])
def test_idn_and_punycode_list_entries_agree(punycode, unicode_form, entry, url_form):
    entry_value = punycode if entry == "punycode" else unicode_form
    payload = f"http://{punycode if url_form == 'punycode' else unicode_form}/x"
    rule = _blacklist(entry_value)
    match = rule.check(payload, 1, 1)
    assert match.matched, f"IDN host evaded the denylist: {payload!r} ({entry} entry)"


@pytest.mark.parametrize("punycode, unicode_form", _IDN_HOSTS)
def test_unlisted_idn_host_fails_whitelist_closed(punycode, unicode_form):
    rule = _whitelist("trusted.example")
    for host in (punycode, unicode_form):
        match = rule.check(f"http://{host}/x", 1, 1)
        assert match.matched, f"unlisted IDN host passed the whitelist: {host!r}"


def test_punycode_tld_is_a_real_host():
    """A punycode ccTLD such as `.xn--p1ai` must not be discarded."""
    host = "xn--e1afmkfd.xn--p1ai"
    rule = _blacklist(host)
    assert host in rule._blacklist
    assert rule._extract_host(f"http://{host}/x") == host
    assert rule.check(f"http://{host}/x", 1, 1).matched
    assert _whitelist("trusted.example").check(f"http://{host}/x", 1, 1).matched


def test_multiple_dns_root_dots_normalize_like_consumer():
    rule = _blacklist("evil.example")
    assert rule._extract_host("http://evil.example../x") == "evil.example"
    assert rule.check("http://evil.example../x", 1, 1).matched


@pytest.mark.parametrize(
    "payload",
    [
        "https://trusted.example/x",  # ordinary allowlisted link
        "https://trusted.example/a%2Fb",  # encoded separator in the PATH is data
        "https://trusted.example/%65vil",  # encoded bytes in the PATH are data
    ],
)
def test_encoded_path_does_not_overblock(payload):
    assert not _whitelist("trusted.example").check(payload, 1, 1).matched
    assert not _blacklist("evil.example").check(payload, 1, 1).matched
