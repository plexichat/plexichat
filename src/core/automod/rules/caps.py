"""
Caps percentage rule.

Detects messages with excessive capital letters.
"""

from typing import Dict, Any, Optional

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


class CapsPercentageRule(BaseRule):
    """Rule that detects excessive caps usage."""

    rule_type = RuleType.CAPS_PERCENTAGE

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_percentage: float = self.config.get("max_percentage", 70.0)
        self._min_length: int = self.config.get("min_length", 10)
        self._ignore_commands: bool = self.config.get("ignore_commands", True)

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None
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
                "uppercase_count": uppercase_count
            },
            severity=severity
        )

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate caps percentage rule configuration."""
        issues = []

        max_pct = config.get("max_percentage", 70.0)
        if not isinstance(max_pct, (int, float)) or not 0 <= max_pct <= 100:
            issues.append("max_percentage must be between 0 and 100")

        min_len = config.get("min_length", 10)
        if not isinstance(min_len, int) or min_len < 1:
            issues.append("min_length must be a positive integer")

        if "ignore_commands" in config and not isinstance(config["ignore_commands"], bool):
            issues.append("ignore_commands must be a boolean")

        return len(issues) == 0, issues
