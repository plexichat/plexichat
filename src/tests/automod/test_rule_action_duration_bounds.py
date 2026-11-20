"""Regression tests: stored rule actions must re-bound timeout duration on read.

``create_rule`` bounds ``duration_seconds`` to ``[0, MAX_DURATION_SECONDS]``,
but ``row_to_rule`` (used by ``get_rule``/``get_rules`` and therefore by the
rule-driven timeout path) copied the raw JSON value straight into ``RuleAction``.
A tampered/legacy rule row could therefore carry an unbounded or negative
timeout past the write-time validation. Re-apply the same bound on read and
drop an invalid value so the executor's safe default is used instead.
"""

import json

import pytest

from src.core.automod.limits import MAX_DURATION_SECONDS
from src.core.automod.manager.converters import row_to_rule


def _row_with_duration(duration):
    actions = [{"action_type": "timeout_user", "duration_seconds": duration}]
    return {
        "id": 1,
        "server_id": 1,
        "name": "stored-rule",
        "rule_type": "keyword",
        "enabled": 1,
        "config": json.dumps({"keywords": ["x"]}),
        "actions": json.dumps(actions),
        "applied_roles": None,
        "exempt_roles": None,
        "exempt_channels": None,
        "priority": 0,
        "created_at": 0,
        "updated_at": 0,
        "created_by": 1,
        "check_all": 0,
    }


@pytest.mark.automod
@pytest.mark.parametrize(
    "bad_duration",
    [MAX_DURATION_SECONDS + 1, -1, 10**30, "9999999999", True, 1.5],
)
def test_stored_rule_duration_is_rebounded_on_read(bad_duration):
    rule = row_to_rule(_row_with_duration(bad_duration))
    assert rule.actions[0].duration_seconds is None


@pytest.mark.automod
def test_stored_rule_keeps_valid_duration():
    rule = row_to_rule(_row_with_duration(60))
    assert rule.actions[0].duration_seconds == 60


@pytest.mark.automod
def test_stored_rule_keeps_duration_at_cap():
    rule = row_to_rule(_row_with_duration(MAX_DURATION_SECONDS))
    assert rule.actions[0].duration_seconds == MAX_DURATION_SECONDS
