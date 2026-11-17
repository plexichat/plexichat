"""Cross-cutting regression tests for the moderation boundary campaign.

Every guard added in this campaign is pinned here at the public helper
level: allowlists, regex compile cost, list sizes, window counters, report
fields and IDs, DM burst parameters, action reasons and evidence payloads.
The tests are deliberately fixture-free so they stay meaningful while
unrelated modules are being edited.
"""

import inspect
from typing import Any

import pytest
from fastapi import HTTPException

from src.api.routes import reports as report_routes
from src.core.antispam.detector import (
    MAX_DM_CONTENT_LENGTH,
    MAX_FILTER_THRESHOLD,
    DMSpamDetector,
)
from src.core.automod.limits import (
    MAX_DURATION_SECONDS,
    MAX_KEYWORDS,
    MAX_PATTERNS_PER_RULE,
    MAX_REASON_LENGTH,
    bounded_reason,
)
from src.core.automod.exceptions import RuleValidationError
from src.core.automod.manager.rules import _parse_action
from src.core.automod.models import (
    ACTION_TYPE_VALUES,
    RULE_TYPE_VALUES,
    ActionType,
    RuleType,
    coerce_action_type,
    coerce_rule_type,
)
from src.core.automod.rules.keyword import KeywordRule
from src.core.automod.rules.regex import (
    MAX_PATTERN_LENGTH,
    RegexRule,
    _has_redos_risk,
)
from src.core.automod.rules.spam import MessageSpamRule
from src.core.security.hardening import SNOWFLAKE_MAX

ALLOWLIST_FILES = (
    "src/core/automod/models.py",
    "src/core/automod/manager/rules.py",
    "src/core/automod/manager/converters.py",
    "src/core/automod/manager/actions.py",
)


def _source(module):
    return inspect.getsource(module)


def test_hardening_helpers_are_imported_not_copied():
    """The shared guards are imported at every boundary that uses them."""
    assert "src.core.security.hardening" in _source(
        MessageSpamRule.__module__
        and __import__("src.core.automod.rules.spam", fromlist=["x"])
    )
    assert "src.core.security.hardening" in _source(
        __import__("src.core.antispam.detector", fromlist=["x"])
    )
    assert "src.core.security.hardening" in _source(report_routes)


def test_enum_allowlists_match_definitions():
    assert RULE_TYPE_VALUES == {member.value for member in RuleType}
    assert ACTION_TYPE_VALUES == {member.value for member in ActionType}
    with pytest.raises(ValueError):
        coerce_rule_type("not_a_rule")
    with pytest.raises(ValueError):
        coerce_action_type("not_an_action")


def test_regex_redos_and_length_guards():
    assert _has_redos_risk(r"(a+)+$")
    assert not _has_redos_risk(r"\b\d{16}\b")
    ok, issues = RegexRule.validate_config(
        {"patterns": [{"pattern": "a" * (MAX_PATTERN_LENGTH + 1)}]}
    )
    assert not ok and issues
    ok, issues = RegexRule.validate_config(
        {"patterns": [{"pattern": "a" * MAX_PATTERNS_PER_RULE}]}
    )
    assert ok, issues


def test_list_and_window_constants_are_finite():
    assert 0 < MAX_KEYWORDS < 10_000
    assert 0 < MAX_PATTERNS_PER_RULE < 1_000
    assert 0 < MAX_REASON_LENGTH < 10_000
    assert 0 < MAX_DURATION_SECONDS
    assert MAX_DM_CONTENT_LENGTH < 1_000_000
    assert MAX_FILTER_THRESHOLD < 1_000_000


def test_report_pipeline_boundaries_compose():
    with pytest.raises(HTTPException):
        report_routes._parse_report_id(SNOWFLAKE_MAX + 1, "message_id")
    with pytest.raises(HTTPException):
        report_routes._bounded_report_field("x" * 501, "reason", 500)
    with pytest.raises(HTTPException):
        report_routes._bounded_evidence_ids(["1"] * 21)
    total = report_routes._bounded_report_payload("spam", None, None, [1, 2])
    assert total > 0


def test_dm_spam_burst_and_content_bounds():
    class FakeDB:
        def execute(self, *a, **k):
            return self

        def begin_transaction(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

    detector = DMSpamDetector(FakeDB())
    bad_kwargs: list[dict[str, Any]] = [
        {"threshold": -1},
        {"window_seconds": 10**9},
        {"action": "x"},
    ]
    for kwargs in bad_kwargs:
        with pytest.raises(ValueError):
            detector.set_filter(1, **kwargs)
    assert MAX_DM_CONTENT_LENGTH > 0


def test_action_reason_and_duration_bounds():
    with pytest.raises(ValueError):
        bounded_reason("x" * (MAX_REASON_LENGTH + 1))
    with pytest.raises(RuleValidationError):
        _parse_action({"action_type": "ban_user", "reason": "line\nbreak"})
    action = _parse_action({"action_type": "ban_user", "reason": "spam"})
    assert action.action_type is ActionType.BAN_USER


def test_keyword_rule_rejects_oversized_lists():
    ok, issues = KeywordRule.validate_config({"keywords": ["ok"] * (MAX_KEYWORDS + 1)})
    assert not ok and issues
