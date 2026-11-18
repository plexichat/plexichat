"""Regression tests for automod fail-open hardening.

* ban/kick owner checks must fail closed when the server row cannot be
  resolved, rather than silently allowing the sanction.
* the caps rule must coerce stored config at construction time: rule configs
  are reloaded from JSON on every start and ``validate_config`` is not re-run,
  so a malformed value must not crash or distort the check pipeline.
"""

import pytest

from src.core.automod.actions.ban import BanUserAction
from src.core.automod.actions.kick import KickUserAction
from src.core.automod.models import (
    ActionType,
    Rule,
    RuleAction,
    RuleType,
    Violation,
    ViolationSeverity,
)
from src.core.automod.rules.caps import CapsPercentageRule


class _OwnerLookupDb:
    """Tiny DB stub used to drive the owner-hierarchy branch directly."""

    def __init__(self, owner_id=None, member=True, already_banned=False):
        self.owner_id = owner_id
        self.member = member
        self.already_banned = already_banned

    def fetch_one(self, sql, params=()):
        if "srv_bans" in sql:
            return {"id": 1} if self.already_banned else None
        if "srv_servers" in sql:
            return {"owner_id": self.owner_id} if self.owner_id is not None else None
        if "srv_members" in sql:
            return {"id": 1} if self.member else None
        return None

    def execute(self, *args, **kwargs):
        return None


def _violation(user_id, server_id=999):
    return Violation(
        id=1,
        server_id=server_id,
        channel_id=1,
        user_id=user_id,
        message_id=None,
        rule_id=1,
        rule_type=RuleType.KEYWORD,
        matched_content="spam",
        actions_taken=[],
        severity=ViolationSeverity.MEDIUM,
        created_at=0,
    )


@pytest.mark.automod
class TestOwnerCheckFailsClosed:
    """A missing/malformed server row must never bypass owner protection."""

    @pytest.mark.parametrize(
        "executor_cls, action_type",
        [
            (BanUserAction, ActionType.BAN_USER),
            (KickUserAction, ActionType.KICK_USER),
        ],
    )
    def test_unresolvable_server_refuses_action(self, executor_cls, action_type):
        executor = executor_cls(_OwnerLookupDb(owner_id=None), None, None, None)
        action = RuleAction(action_type=action_type)
        allowed, reason = executor.can_execute(action, _violation(user_id=42))
        assert allowed is False
        assert "owner" in reason.lower()

    @pytest.mark.parametrize(
        "executor_cls, action_type",
        [
            (BanUserAction, ActionType.BAN_USER),
            (KickUserAction, ActionType.KICK_USER),
        ],
    )
    def test_owner_row_still_blocks_targeting_owner(self, executor_cls, action_type):
        executor = executor_cls(_OwnerLookupDb(owner_id=42), None, None, None)
        action = RuleAction(action_type=action_type)
        allowed, reason = executor.can_execute(action, _violation(user_id=42))
        assert allowed is False
        assert "owner" in reason.lower()

    @pytest.mark.parametrize(
        "executor_cls, action_type",
        [
            (BanUserAction, ActionType.BAN_USER),
            (KickUserAction, ActionType.KICK_USER),
        ],
    )
    def test_regular_member_remains_actionable(self, executor_cls, action_type):
        executor = executor_cls(_OwnerLookupDb(owner_id=1), None, None, None)
        action = RuleAction(action_type=action_type)
        allowed, reason = executor.can_execute(action, _violation(user_id=42))
        assert allowed is True
        assert reason is None


def _caps_rule(config):
    return CapsPercentageRule(
        Rule(
            id=1,
            server_id=1,
            name="caps",
            rule_type=RuleType.CAPS_PERCENTAGE,
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
    )


@pytest.mark.automod
class TestCapsConfigHardening:
    """Malformed stored config must not crash or distort rule matching."""

    @pytest.mark.parametrize(
        "bad_config",
        [
            {"max_percentage": "70"},
            {"max_percentage": None},
            {"max_percentage": True},
            {"max_percentage": float("nan")},
            {"max_percentage": float("inf")},
            {"max_percentage": -5},
            {"max_percentage": 150},
            {"min_length": "10"},
            {"min_length": -3},
            {"min_length": True},
            {"min_length": 10**6},
            {"ignore_commands": "false"},
        ],
    )
    def test_malformed_stored_config_never_crashes_check(self, bad_config):
        rule = _caps_rule(bad_config)
        match = rule.check("THIS IS A VERY LOUD UPPERCASE MESSAGE", 1, 1)
        assert match.matched in (True, False)

    def test_invalid_percentage_falls_back_to_default(self):
        rule = _caps_rule({"max_percentage": "oops"})
        assert rule._max_percentage == 70.0

    def test_out_of_range_values_are_clamped(self):
        rule = _caps_rule({"max_percentage": 150, "min_length": 10**6})
        assert rule._max_percentage == 100.0
        assert rule._min_length == 10_000

    def test_valid_config_still_detects_caps(self):
        rule = _caps_rule({"max_percentage": 50, "min_length": 3})
        assert rule.check("HELLO", 1, 1).matched is True
        assert rule.check("hello", 1, 1).matched is False

    @pytest.mark.parametrize(
        "bad_config",
        [
            {"max_percentage": float("nan")},
            {"max_percentage": True},
            {"min_length": True},
            {"min_length": 10**6},
        ],
    )
    def test_validate_config_rejects_malformed_values(self, bad_config):
        valid, issues = CapsPercentageRule.validate_config(bad_config)
        assert valid is False
        assert issues
