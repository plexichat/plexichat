"""Regression tests for FQDN-form entries in link whitelist/blacklist.

Observed hosts are lowercased with the trailing DNS-root dot stripped, but
whitelist/blacklist entries used to be lowercased only. An admin-supplied
FQDN-form entry such as ``"evil.com."`` (the form DNS tooling commonly
emits) therefore never matched ``"evil.com"``: in blacklist mode the entry
was silently dead and the domain slipped through; in whitelist mode a
legitimately listed domain was wrongly blocked.
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


def _blacklist_rule(*entries):
    return ExternalLinkRule(
        _rule(
            RuleType.EXTERNAL_LINKS,
            {"mode": "blacklist", "blacklist": list(entries)},
        )
    )


def _whitelist_rule(*entries):
    return ExternalLinkRule(
        _rule(
            RuleType.EXTERNAL_LINKS,
            {"mode": "whitelist", "whitelist": list(entries)},
        )
    )


class TestListEntryNormalization:
    def test_entries_are_stored_normalized(self):
        rule = _blacklist_rule("Evil.COM. ", "  sub.example.com.")
        assert rule._blacklist == {"evil.com", "sub.example.com"}

    def test_dotted_blacklist_entry_blocks_bare_domain(self):
        match = _blacklist_rule("evil.com.").check("visit evil.com/x today", 1, 1)
        assert match.matched
        assert match.match_details["blocked_domains"] == ["evil.com"]

    def test_dotted_blacklist_entry_blocks_dotted_and_port_forms(self):
        rule = _blacklist_rule("evil.com.")
        for text in (
            "visit evil.com./x today",
            "visit evil.com:8080/x today",
            "visit sub.evil.com/a today",
        ):
            match = rule.check(text, 1, 1)
            assert match.matched, text

    def test_dotted_whitelist_entry_allows_listed_domain(self):
        rule = _whitelist_rule("Example.COM. ")
        for text in (
            "see https://example.com/a",
            "see https://example.com./a",
            "see https://api.example.com/a",
            "see example.com:8080/a",
        ):
            assert not rule.check(text, 1, 1).matched, text

    def test_dotted_whitelist_entry_still_blocks_others(self):
        match = _whitelist_rule("example.com.").check("see evil.com/x", 1, 1)
        assert match.matched
        assert match.match_details["blocked_domains"] == ["evil.com"]

    def test_plain_entries_unchanged(self):
        rule = _blacklist_rule("evil.com")
        assert rule._blacklist == {"evil.com"}
        assert rule.check("visit evil.com/x", 1, 1).matched
