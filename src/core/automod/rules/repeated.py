"""
Repeated characters detection rule.

Detects messages with excessive repeated characters (e.g., "hellooooooo").
"""

import re
from typing import Dict, Any, Optional

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


class RepeatedCharsRule(BaseRule):
    """Rule that detects excessive repeated characters."""

    rule_type = RuleType.REPEATED_CHARS

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_repeats: int = self.config.get("max_repeats", 5)
        self._min_occurrences: int = self.config.get("min_occurrences", 3)
        self._ignore_whitespace: bool = self.config.get("ignore_whitespace", True)
        self._ignore_numbers: bool = self.config.get("ignore_numbers", False)

        pattern = rf"(.)\1{{{self._max_repeats - 1},}}"
        self._pattern = re.compile(pattern, re.IGNORECASE)

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None
    ) -> RuleMatch:
        """Check for excessive repeated characters."""
        matches = self._pattern.findall(content)

        if not matches:
            return self._no_match()

        violations = []
        for char in matches:
            if self._ignore_whitespace and char.isspace():
                continue
            if self._ignore_numbers and char.isdigit():
                continue
            violations.append(char)

        if len(violations) < self._min_occurrences:
            return self._no_match()

        full_matches = self._pattern.finditer(content)
        examples = []
        for m in full_matches:
            matched_text = m.group()
            if len(matched_text) > 10:
                matched_text = matched_text[:10] + "..."
            examples.append(matched_text)
            if len(examples) >= 3:
                break

        return self._create_match(
            matched=True,
            matched_content=", ".join(examples),
            details={
                "repeated_chars": violations,
                "occurrence_count": len(violations),
                "max_allowed": self._max_repeats,
                "examples": examples
            },
            severity=ViolationSeverity.LOW
        )

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate repeated chars rule configuration."""
        issues = []

        max_repeats = config.get("max_repeats", 5)
        if not isinstance(max_repeats, int) or max_repeats < 2:
            issues.append("max_repeats must be an integer >= 2")

        min_occurrences = config.get("min_occurrences", 3)
        if not isinstance(min_occurrences, int) or min_occurrences < 1:
            issues.append("min_occurrences must be a positive integer")

        for field in ["ignore_whitespace", "ignore_numbers"]:
            if field in config and not isinstance(config[field], bool):
                issues.append(f"{field} must be a boolean")

        return len(issues) == 0, issues
