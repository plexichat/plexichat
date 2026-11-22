"""
Caps percentage rule.

Detects messages with excessive capital letters.
"""

import math
from typing import Dict, Any, Optional

from .base import BaseRule
from ..limits import MAX_MIN_LENGTH
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity

DEFAULT_MAX_PERCENTAGE = 70.0
DEFAULT_MIN_LENGTH = 10


def _bounded_float(value: Any, default: float, low: float, high: float) -> float:
    """Coerce an untrusted numeric config value into a finite, bounded float."""
    # bool is a subclass of int but is never a meaningful percentage.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    if not math.isfinite(value):
        return default
    return min(max(value, low), high)


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    """Coerce an untrusted config value into a bounded int."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(value, low), high)


class CapsPercentageRule(BaseRule):
    """Rule that detects excessive caps usage."""

    rule_type = RuleType.CAPS_PERCENTAGE

    def __init__(self, rule: Rule):
        super().__init__(rule)
        import utils.config as config

        # Rule configs are stored as JSON and are rebuilt into rule objects on
        # every process start. validate_config() only runs on create/update, so
        # a stale or hand-edited row can reach construction carrying arbitrary
        # values. Coerce and bound them here: without this, a string/NaN/None
        # raises a TypeError inside check() and takes down the whole message
        # check pipeline, while a negative or oversized threshold produces
        # nonsensical (or unbounded) matching. This mirrors the
        # construction-time allowlisting performed by AIModerationRule.
        max_percentage = self.config.get(
            "max_percentage",
            config.get("automod.rules.caps.max_percentage", DEFAULT_MAX_PERCENTAGE),
        )
        min_length = self.config.get(
            "min_length",
            config.get("automod.rules.caps.min_length", DEFAULT_MIN_LENGTH),
        )
        ignore_commands = self.config.get(
            "ignore_commands",
            config.get("automod.rules.caps.ignore_commands", True),
        )
        self._max_percentage: float = _bounded_float(
            max_percentage, DEFAULT_MAX_PERCENTAGE, 0.0, 100.0
        )
        self._min_length: int = _bounded_int(
            min_length, DEFAULT_MIN_LENGTH, 1, MAX_MIN_LENGTH
        )
        self._ignore_commands: bool = (
            ignore_commands if isinstance(ignore_commands, bool) else True
        )

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        """Check for excessive caps."""
        if self._ignore_commands and content.startswith(("/", "!", ".")):
            return self._no_match()

        letters = [c for c in content if c.isalpha()]

        if len(letters) < self._min_length:
            return self._no_match()

        uppercase_count = sum(1 for c in letters if c.isupper())
        caps_percentage = (uppercase_count / len(letters)) * 100

        if caps_percentage <= self._max_percentage:
            return self._no_match()

        severity = ViolationSeverity.LOW
        if caps_percentage > 90:
            severity = ViolationSeverity.MEDIUM

        return self._create_match(
            matched=True,
            matched_content=f"{caps_percentage:.1f}% caps",
            details={
                "caps_percentage": round(caps_percentage, 1),
                "threshold": self._max_percentage,
                "letter_count": len(letters),
                "uppercase_count": uppercase_count,
            },
            severity=severity,
        )

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate caps percentage rule configuration."""
        issues = []

        max_pct = config.get("max_percentage", DEFAULT_MAX_PERCENTAGE)
        if (
            isinstance(max_pct, bool)
            or not isinstance(max_pct, (int, float))
            or not math.isfinite(max_pct)
            or not 0 <= max_pct <= 100
        ):
            issues.append("max_percentage must be a finite number between 0 and 100")

        min_len = config.get("min_length", DEFAULT_MIN_LENGTH)
        if (
            isinstance(min_len, bool)
            or not isinstance(min_len, int)
            or not 1 <= min_len <= MAX_MIN_LENGTH
        ):
            issues.append("min_length must be a positive integer")

        if "ignore_commands" in config and not isinstance(
            config["ignore_commands"], bool
        ):
            issues.append("ignore_commands must be a boolean")

        return len(issues) == 0, issues
