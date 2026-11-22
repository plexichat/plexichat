"""Regression: stored invite-rule configs must not break or grow unbounded.

``validate_config`` caps ``code_length`` and ``allowed_codes`` on create/update,
but stored rule configs are rebuilt into rule objects on every worker start
without revalidation. ``InviteLinkRule.__init__`` used to interpolate the raw
value into a regex quantifier, so a stale or hand-edited row with
``code_length = 10**10`` raised ``OverflowError`` inside rule construction and
took out message moderation for that server; a multi-thousand-entry
``allowed_codes`` list was loaded into every process.

The bounds applied at construction mirror validate_config and the
construction-time fallbacks already used by the keyword/repeat/mention rules.
Validation of caller input is unchanged and is asserted below.
"""

import pytest

from src.core.automod.limits import MAX_CODE_LENGTH
from src.core.automod.models import (
    ActionType,
    Rule,
    RuleAction,
    RuleType,
)
from src.core.automod.rules.links import (
    MAX_ALLOWED_CODES,
    InviteLinkRule,
)


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="invite test",
        rule_type=RuleType.INVITE_LINKS,
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


@pytest.mark.parametrize(
    "bad_value",
    [
        10**10,  # used to raise OverflowError at re.compile time
        -1,  # negative quantifier - pattern never matched
        0,
        65,  # above MAX_CODE_LENGTH
        1.5,  # fractional
        "8",  # non-integer type from a hand-edited JSON row
        "not-a-number",
        True,  # bool is not a valid integer here
        float("inf"),
        None,
    ],
)
def test_malformed_stored_code_length_falls_back_to_default(bad_value):
    rule = InviteLinkRule(_rule({"code_length": bad_value}))
    assert rule._code_length == 8
    assert rule._pattern.pattern == r"\b([a-zA-Z0-9]{8})\b"
    # The default pattern still enforces the rule; the malformed config does
    # not turn enforcement off.
    match = rule.check("join discord.gg/ABCDEFGH now", 1, 1)
    assert match.matched


@pytest.mark.parametrize("valid_value", [1, 5, 8, MAX_CODE_LENGTH])
def test_valid_stored_code_length_is_honored(valid_value):
    rule = InviteLinkRule(_rule({"code_length": valid_value}))
    assert rule._code_length == valid_value
    assert rule._pattern.pattern == rf"\b([a-zA-Z0-9]{{{valid_value}}})\b"


def test_oversized_and_malformed_allowed_codes_are_bounded():
    # Stored row carried thousands of entries plus non-string values; the old
    # constructor loaded all of them and raised on the non-string entry.
    rule = InviteLinkRule(
        _rule(
            {
                "allowed_codes": ["ABCDEFGH"] * (MAX_ALLOWED_CODES * 25)
                + [None, 7, ""],
            }
        )
    )
    assert len(rule._allowed_codes) == 1
    assert "abcdefgh" in rule._allowed_codes
    # A listed code stays allowlisted - the hardening must not weaken checks.
    assert not rule.check("discord.gg/ABCDEFGH", 1, 1).matched
    # An unlisted code is still blocked.
    assert rule.check("discord.gg/ZYXWVUTS", 1, 1).matched


def test_non_string_allowed_codes_value_is_ignored():
    rule = InviteLinkRule(_rule({"allowed_codes": "ABCDEFGH"}))
    assert rule._allowed_codes == set()


def test_non_string_custom_pattern_falls_back_without_crashing():
    rule = InviteLinkRule(_rule({"pattern": ["not-a-string"]}))
    # Compare the compiled form, not object identity: ``re.compile`` returns the
    # same object only while its bounded internal cache still holds the pattern,
    # so an identity check fails once the rest of the suite has evicted it.
    assert rule._pattern.pattern == InviteLinkRule.DEFAULT_INVITE_PATTERN.pattern
    assert rule._pattern.flags == InviteLinkRule.DEFAULT_INVITE_PATTERN.flags
    # Caller-facing validation still rejects the malformed pattern.
    valid, issues = InviteLinkRule.validate_config({"pattern": ["not-a-string"]})
    assert valid is False
    assert issues


def test_valid_config_still_validates():
    valid, issues = InviteLinkRule.validate_config(
        {"code_length": 8, "allowed_codes": ["ABCDEFGH"], "block_all": True}
    )
    assert valid is True, issues
