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
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        """Check for excessive repeated characters.

        ``min_occurrences`` counts OCCURRENCES, not distinct runs: every
        character of a run beyond ``max_repeats`` counts as one occurrence.
        A single "a" * 20 run with max_repeats=5 therefore contributes 15
        occurrences and triggers min_occurrences=3 on its own.
        """
        violations = []
        examples = []
        total_occurrences = 0

        for m in self._pattern.finditer(content):
            char = m.group(1)
            if self._ignore_whitespace and char.isspace():
                continue
            if self._ignore_numbers and char.isdigit():
                continue

            total_occurrences += max(0, len(m.group()) - self._max_repeats)

            matched_text = m.group()
            if len(matched_text) > 10:
                matched_text = matched_text[:10] + "..."
            if len(examples) < 3:
                examples.append(matched_text)
            violations.append(char)

        if total_occurrences < self._min_occurrences:
            return self._no_match()

        return self._create_match(
            matched=True,
            matched_content=", ".join(examples),
            details={
                "repeated_chars": violations,
                "occurrence_count": total_occurrences,
                "max_allowed": self._max_repeats,
                "examples": examples,
            },
            severity=ViolationSeverity.LOW,
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
