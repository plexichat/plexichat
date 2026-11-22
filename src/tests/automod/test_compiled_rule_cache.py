"""Regression tests for per-message automod rule compilation.

Before the cache, ``EvaluationMixin._evaluate_rule`` called ``rule_class(rule)``
for every message. That recompiled regex patterns, re-ran the ReDoS static
analysis, re-canonicalized keywords and rebuilt domain sets on a hot path that
only depends on the rule's *content*. These tests pin the "compile once per
content/revision" behaviour deterministically (construction counts, not flaky
wall-clock thresholds) and cover invalidation and bounding.
"""

import time

from src.core.automod.manager.evaluation import EvaluationMixin
from src.core.automod.models import Rule, RuleType
from src.core.automod.rules.compiled_cache import CompiledRuleCache, rule_content_key
from src.core.automod.rules.regex import RegexRule


def _rule(
    rule_id=42,
    config=None,
    rule_type=RuleType.KEYWORD,
    name="cached rule",
    updated_at=100,
):
    return Rule(
        id=rule_id,
        server_id=1,
        name=name,
        rule_type=rule_type,
        enabled=True,
        config=config if config is not None else {"keywords": ["bad"]},
        actions=[],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=1,
        created_at=0,
        updated_at=updated_at,
        created_by=1,
    )


class _CountingRule:
    """Stand-in that only counts constructions (no logger/DB dependencies)."""

    builds = 0

    def __init__(self, rule):
        type(self).builds += 1
        self.rule = rule

    def check(self, content, user_id, channel_id, context=None):
        return ("checked", self.rule.id)


class _CountingRegexRule(RegexRule):
    """Real RegexRule with a construction counter, to prove compiles are skipped."""

    builds = 0

    def __init__(self, rule):
        type(self).builds += 1
        super().__init__(rule)


class _Manager(EvaluationMixin):
    RULE_CLASSES = {RuleType.KEYWORD: _CountingRule, RuleType.REGEX: _CountingRegexRule}

    def __init__(self):
        self._compiled_rules = CompiledRuleCache(max_entries=8)


# --------------------------------------------------------------------------- #
# Content key                                                                   #
# --------------------------------------------------------------------------- #


def test_content_key_is_stable_across_deserialized_rule_objects():
    a = _rule(config={"keywords": ["a", "b"], "whole_word": True})
    b = _rule(config={"whole_word": True, "keywords": ["a", "b"]})
    assert rule_content_key(a, _CountingRule) == rule_content_key(b, _CountingRule)


def test_content_key_tracks_config_and_revision_changes():
    base = rule_content_key(_rule(), _CountingRule)
    changed_config = rule_content_key(
        _rule(config={"keywords": ["different"]}), _CountingRule
    )
    changed_revision = rule_content_key(_rule(updated_at=101), _CountingRule)
    changed_name = rule_content_key(_rule(name="renamed"), _CountingRule)
    assert len({base, changed_config, changed_revision, changed_name}) == 4


# --------------------------------------------------------------------------- #
# Cache behaviour                                                               #
# --------------------------------------------------------------------------- #


def test_same_content_compiles_once_even_for_fresh_rule_objects():
    _CountingRule.builds = 0
    cache = CompiledRuleCache(max_entries=8)

    first = cache.get(_rule(), _CountingRule)
    # Simulate the DB round trip: a brand-new Rule object with identical content.
    second = cache.get(_rule(), _CountingRule)

    assert first is second
    assert _CountingRule.builds == 1
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_config_or_revision_change_forces_recompilation():
    _CountingRule.builds = 0
    cache = CompiledRuleCache(max_entries=8)

    v1 = cache.get(_rule(config={"keywords": ["a"]}, updated_at=1), _CountingRule)
    v2 = cache.get(_rule(config={"keywords": ["b"]}, updated_at=2), _CountingRule)
    v3 = cache.get(_rule(config={"keywords": ["b"]}, updated_at=3), _CountingRule)
    v4 = cache.get(_rule(config={"keywords": ["b"]}, updated_at=3), _CountingRule)

    assert len({id(v1), id(v2), id(v3)}) == 3
    assert v3 is v4
    assert _CountingRule.builds == 3


def test_cache_stays_bounded_under_distinct_rule_churn():
    cache = CompiledRuleCache(max_entries=4)
    for i in range(50):
        cache.get(_rule(rule_id=1000 + i), _CountingRule)

    assert len(cache) == 4
    stats = cache.stats()
    assert stats["evictions"] == 46
    assert stats["max_entries"] == 4


def test_clear_drops_all_compiled_instances():
    cache = CompiledRuleCache(max_entries=8)
    cache.get(_rule(), _CountingRule)
    assert len(cache) == 1
    cache.clear()
    assert len(cache) == 0
    assert cache.stats()["hits"] == 0
    assert cache.stats()["misses"] == 1


# --------------------------------------------------------------------------- #
# Evaluation hot path                                                           #
# --------------------------------------------------------------------------- #


def test_evaluate_rule_reuses_compiled_instance_across_messages():
    _CountingRule.builds = 0
    manager = _Manager()
    rule = _rule()

    results = [
        manager._evaluate_rule(rule, f"message {i}", 7, 9, {}) for i in range(25)
    ]

    assert _CountingRule.builds == 1
    assert all(result == ("checked", rule.id) for result in results)


def test_evaluate_rule_compiles_real_regex_once():
    _CountingRegexRule.builds = 0
    manager = _Manager()
    rule = _rule(
        rule_type=RuleType.REGEX,
        config={
            "patterns": [{"name": "cc", "pattern": r"\b\d{16}\b", "severity": "high"}]
        },
    )

    first = manager._evaluate_rule(rule, "no match here", 7, 9, {})
    second = manager._evaluate_rule(rule, "still no match", 7, 9, {})

    assert _CountingRegexRule.builds == 1
    assert not first.matched
    assert not second.matched


def test_deliberate_compile_cost_is_paid_once():
    """Timing analogue of the project's perf tests: a slow constructor must
    not be paid on every message. The sleep makes the margin unambiguous."""
    sleep_cost = 0.02
    reads = 20

    class _SlowRule:
        def __init__(self, rule):
            time.sleep(sleep_cost)

        def check(self, *args, **kwargs):
            return None

    cache = CompiledRuleCache(max_entries=8)
    rule = _rule()

    start = time.perf_counter()
    for _ in range(reads):
        cache.get(rule, _SlowRule)
    elapsed = time.perf_counter() - start

    # Uncached this would cost ~0.4s; one compile is ~0.02s plus overhead.
    assert elapsed < reads * sleep_cost / 4
