"""Regression tests for the trailing-dot FQDN bypass in link filtering.

A trailing dot is the DNS root marker: ``evil.com.`` resolves exactly as
``evil.com``. ``ExternalLinkRule._extract_host`` used to reject the dotted
form (it fails the bare-host shape), so ``evil.com./x`` was reported as
"not a link at all" and slipped past both blacklist and whitelist modes
while ``evil.com/x`` was blocked.
"""

from src.core.automod.models import (
    Rule,
    RuleAction,
    ActionType,
    RuleType,
)
from src.core.automod.rules.links import ExternalLinkRule


def _rule(rule_type, config):
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
        _rule(RuleType.EXTERNAL_LINKS, {"mode": "blacklist", "blacklist": ["evil.com"]})
    )


class TestTrailingDotFqdn:
    def test_extract_host_strips_dns_root_dot(self):
        rule = _blacklist_rule()
        assert rule._extract_host("evil.com./x") == "evil.com"
        assert rule._extract_host("evil.com.") == "evil.com"
        assert rule._extract_host("sub.evil.com./a") == "sub.evil.com"

    def test_blacklist_blocks_dotted_form(self):
        match = _blacklist_rule().check("visit evil.com./x today", 1, 1)
        assert match.matched
        assert match.match_details["blocked_domains"] == ["evil.com"]

    def test_whitelist_blocks_dotted_form(self):
        rule = ExternalLinkRule(
            _rule(
                RuleType.EXTERNAL_LINKS,
                {"mode": "whitelist", "whitelist": ["example.com"]},
            )
        )
        match = rule.check("see evil.com./x", 1, 1)
        assert match.matched
        assert match.match_details["blocked_domains"] == ["evil.com"]

    def test_whitelisted_dotted_form_stays_allowed(self):
        rule = ExternalLinkRule(
            _rule(
                RuleType.EXTERNAL_LINKS,
                {"mode": "whitelist", "whitelist": ["example.com"]},
            )
        )
        assert not rule.check("see example.com./a", 1, 1).matched

    def test_dotted_form_with_port_and_scheme(self):
        rule = _blacklist_rule()
        assert rule._extract_host("http://evil.com./x") == "evil.com"
        assert rule._extract_host("evil.com.:8080/x") == "evil.com"
        assert rule._extract_host("www.evil.com./x") == "evil.com"

    def test_lone_dot_is_not_a_link(self):
        assert _blacklist_rule()._extract_host(".") is None
