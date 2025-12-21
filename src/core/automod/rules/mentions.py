"""
Mention spam detection rule.

Detects excessive user mentions, role mentions, and @everyone/@here abuse.
"""

import re
from typing import Dict, Any, Optional

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


class MentionSpamRule(BaseRule):
    """Rule that detects mention spam."""

    rule_type = RuleType.MENTION_SPAM

    USER_MENTION_PATTERN = re.compile(r"<@!?(\d+)>")
    ROLE_MENTION_PATTERN = re.compile(r"<@&(\d+)>")
    EVERYONE_PATTERN = re.compile(r"@(everyone|here)\b", re.IGNORECASE)

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_user_mentions: int = self.config.get("max_user_mentions", 5)
        self._max_role_mentions: int = self.config.get("max_role_mentions", 3)
        self._max_total_mentions: int = self.config.get("max_total_mentions", 10)
        self._block_everyone: bool = self.config.get("block_everyone", False)
        self._count_unique_only: bool = self.config.get("count_unique_only", False)

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None
    ) -> RuleMatch:
        """Check for mention spam."""
        user_mentions = self.USER_MENTION_PATTERN.findall(content)
        role_mentions = self.ROLE_MENTION_PATTERN.findall(content)
        everyone_mentions = self.EVERYONE_PATTERN.findall(content)

        if self._count_unique_only:
            user_mentions = list(set(user_mentions))
            role_mentions = list(set(role_mentions))

        user_count = len(user_mentions)
        role_count = len(role_mentions)
        everyone_count = len(everyone_mentions)
        total_count = user_count + role_count

        violations = []
        highest_severity = ViolationSeverity.LOW

        if user_count > self._max_user_mentions:
            violations.append(f"{user_count} user mentions (max {self._max_user_mentions})")
            highest_severity = ViolationSeverity.MEDIUM

        if role_count > self._max_role_mentions:
            violations.append(f"{role_count} role mentions (max {self._max_role_mentions})")
            highest_severity = ViolationSeverity.MEDIUM

        if total_count > self._max_total_mentions:
            violations.append(f"{total_count} total mentions (max {self._max_total_mentions})")
            highest_severity = ViolationSeverity.HIGH

        if self._block_everyone and everyone_count > 0:
            violations.append("@everyone/@here usage blocked")
            highest_severity = ViolationSeverity.HIGH

        if not violations:
            return self._no_match()

        return self._create_match(
            matched=True,
            matched_content="; ".join(violations),
            details={
                "user_mentions": user_count,
                "role_mentions": role_count,
                "everyone_mentions": everyone_count,
                "total_mentions": total_count,
                "violations": violations
            },
            severity=highest_severity
        )

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate mention spam rule configuration."""
        issues = []

        for field in ["max_user_mentions", "max_role_mentions", "max_total_mentions"]:
            value = config.get(field)
            if value is not None:
                if not isinstance(value, int) or value < 0:
                    issues.append(f"{field} must be a non-negative integer")

        if "block_everyone" in config and not isinstance(config["block_everyone"], bool):
            issues.append("block_everyone must be a boolean")

        if "count_unique_only" in config and not isinstance(config["count_unique_only"], bool):
            issues.append("count_unique_only must be a boolean")

        return len(issues) == 0, issues
