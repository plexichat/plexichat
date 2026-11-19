"""Regression tests for automod text-matching and manual-action hardening.

Two real weaknesses are covered:

1. Keyword and duplicate-spam rules compared raw ``str.lower()`` text, so a
   sender could evade a blocklist/duplicate counter by splicing invisible
   Unicode into a word or using fullwidth compatibility forms.
2. ``trigger_action`` (manual actions) did not clamp ``duration_seconds`` the
   way the rule-config path does, allowing unbounded/negative/non-integer
   timeouts.
"""

import time

import pytest

from src.core.automod.canonicalization import canonicalize_for_matching
from src.core.automod.limits import MAX_DURATION_SECONDS
from src.core.automod.models import ActionType, Rule, RuleType
from src.core.automod.rules.keyword import KeywordRule
from src.core.automod.rules.spam import MessageSpamRule


def _rule(rule_type, config):
    """Build a minimal Rule without touching the database."""
    return Rule(
        id=1,
        server_id=1,
        name="hardening-regression",
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
        created_by=1,
    )


@pytest.mark.automod
@pytest.mark.parametrize(
    "content",
    [
        "buy badword now",  # control: plain hit
        "buy bad\u200bword now",  # zero-width space
        "buy bad\u200dword now",  # zero-width joiner
        "buy bad\u00adword now",  # soft hyphen
        "buy \ufeffbadword now",  # BOM
        "buy \uff42\uff41\uff44\uff57\uff4f\uff52\uff44 now",  # fullwidth
    ],
)
def test_keyword_rule_matches_normalized_variants(content):
    """Invisible/fullwidth variants of a blocked word must still match."""
    rule = KeywordRule(
        _rule(
            RuleType.KEYWORD,
            {"keywords": ["badword"], "case_sensitive": False, "whole_word": True},
        )
    )
    assert rule.check(content, user_id=1, channel_id=1).matched is True


@pytest.mark.automod
def test_keyword_rule_does_not_flag_unrelated_content():
    """Canonicalization must not turn the blocklist into a match-all."""
    rule = KeywordRule(
        _rule(
            RuleType.KEYWORD,
            {"keywords": ["badword"], "case_sensitive": False, "whole_word": True},
        )
    )
    assert (
        rule.check("a perfectly clean message", user_id=1, channel_id=1).matched
        is False
    )


@pytest.mark.automod
def test_canonicalize_strips_invisible_and_folds_width():
    assert (
        canonicalize_for_matching("B\u200bad\ufeff\uff57\uff4f\uff52\uff44")
        == "badword"
    )


@pytest.mark.automod
def test_duplicate_spam_detects_zero_width_variants():
    """Visually identical messages with varied invisibles still count as dupes."""
    rule = MessageSpamRule(
        _rule(
            RuleType.MESSAGE_SPAM,
            {
                "max_messages": 100,  # keep the rate-spam check from firing
                "window_seconds": 1,
                "duplicate_threshold": 2,
                "duplicate_window_seconds": 60,
            },
        )
    )
    now = int(time.time() * 1000)
    context = {
        "recent_messages": [
            {"user_id": 7, "created_at": now, "content": "buy bad\u200bword now"},
            {"user_id": 7, "created_at": now, "content": "buy b\u200badword now"},
        ]
    }
    assert (
        rule.check(
            "buy bad\u200dword now", user_id=7, channel_id=1, context=context
        ).matched
        is True
    )


@pytest.mark.automod
@pytest.mark.parametrize(
    "bad_duration",
    [MAX_DURATION_SECONDS + 1, -1, 10**30, "9999999999", True, 1.5],
)
def test_manual_action_rejects_invalid_duration(
    automod_manager, test_server, user_pool, modules, bad_duration
):
    """Manual timeouts must honour the same bounds as rule-config actions."""
    server, owner = test_server
    victim = user_pool.get_user()
    modules.servers.add_member(server.id, victim.id)

    assert (
        automod_manager.trigger_action(
            user_id=owner.id,
            server_id=server.id,
            target_user_id=victim.id,
            action_type=ActionType.TIMEOUT_USER,
            reason="duration bound regression",
            duration_seconds=bad_duration,
        )
        is False
    )
    # Refused before execution, so nothing was audited either.
    assert automod_manager.get_audit_log(server.id) == []


@pytest.mark.automod
def test_manual_action_accepts_duration_at_cap(
    automod_manager, test_server, user_pool, modules
):
    """A valid, in-range duration must still be accepted."""
    server, owner = test_server
    victim = user_pool.get_user()
    modules.servers.add_member(server.id, victim.id)

    assert (
        automod_manager.trigger_action(
            user_id=owner.id,
            server_id=server.id,
            target_user_id=victim.id,
            action_type=ActionType.TIMEOUT_USER,
            reason="duration bound regression",
            duration_seconds=MAX_DURATION_SECONDS,
        )
        is True
    )
    assert automod_manager.get_audit_log(server.id)
