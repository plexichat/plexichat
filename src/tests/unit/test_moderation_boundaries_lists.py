"""Keyword and domain lists have finite, bounded sizes."""

import pytest

from src.core.automod.limits import (
    MAX_DOMAIN_LENGTH,
    MAX_KEYWORD_LENGTH,
    MAX_KEYWORDS,
    MAX_LISTED_DOMAINS,
    MAX_SEVERITY_MAP_ENTRIES,
)
from src.core.automod.models import Rule, RuleType
from src.core.automod.rules.links import ExternalLinkRule
from src.core.automod.rules.keyword import KeywordRule


def _rule(config, rule_type=RuleType.KEYWORD):
    return Rule(
        id=1,
        server_id=2,
        name="r",
        rule_type=rule_type,
        enabled=True,
        config=config,
        actions=[],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=3,
    )


def test_keyword_list_length_cap():
    config = {"keywords": ["ok"] * (MAX_KEYWORDS + 1)}
    ok, issues = KeywordRule.validate_config(config)
    assert not ok and any("exceeds" in i for i in issues)
    assert len(KeywordRule(_rule(config))._keywords) == MAX_KEYWORDS


def test_keyword_field_length_cap():
    long_word = "a" * (MAX_KEYWORD_LENGTH + 1)
    ok, issues = KeywordRule.validate_config({"keywords": [long_word]})
    assert not ok and issues
    assert KeywordRule(_rule({"keywords": [long_word, "fine"]}))._keywords == ["fine"]


def test_severity_map_bounded():
    config = {
        "keywords": ["ok"],
        "severity_map": {f"k{i}": "low" for i in range(MAX_SEVERITY_MAP_ENTRIES + 1)},
    }
    ok, issues = KeywordRule.validate_config(config)
    assert not ok and issues
    rule = KeywordRule(_rule(config))
    assert len(rule._severity_map) <= MAX_SEVERITY_MAP_ENTRIES


def test_domain_lists_bounded():
    ok, issues = ExternalLinkRule.validate_config(
        {"whitelist": ["a.com"] * (MAX_LISTED_DOMAINS + 1)}
    )
    assert not ok and issues
    ok, issues = ExternalLinkRule.validate_config(
        {"blacklist": ["a" * (MAX_DOMAIN_LENGTH + 1)]}
    )
    assert not ok and issues
    rule = ExternalLinkRule(
        _rule(
            {"mode": "blacklist", "whitelist": ["a.com"] * (MAX_LISTED_DOMAINS + 5)},
            RuleType.EXTERNAL_LINKS,
        )
    )
    assert len(rule._whitelist) == 1
