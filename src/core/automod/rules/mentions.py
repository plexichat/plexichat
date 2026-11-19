"""
Mention spam detection rule.

Detects excessive user mentions, role mentions, and @everyone/@here abuse.
"""

import re
from typing import Dict, Any, Optional

from src.core.security.hardening import HardeningViolation, bounded_int

from .base import BaseRule
from ..canonicalization import fold_invisible_for_matching
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


def _bounded(value, field, default):
    try:
        return bounded_int(value, field, minimum=0, maximum=10_000, default=default)
    except HardeningViolation:
        return default


class MentionSpamRule(BaseRule):
    """Rule that detects mention spam."""

    rule_type = RuleType.MENTION_SPAM

    USER_MENTION_PATTERN = re.compile(r"<@!?(\d+)>")
    ROLE_MENTION_PATTERN = re.compile(r"<@&(\d+)>")
    EVERYONE_PATTERN = re.compile(r"@(everyone|here)\b", re.IGNORECASE)

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_user_mentions: int = _bounded(
            self.config.get("max_user_mentions"), "max_user_mentions", 5
        )
        self._max_role_mentions: int = _bounded(
            self.config.get("max_role_mentions"), "max_role_mentions", 3
        )
        self._max_total_mentions: int = _bounded(
            self.config.get("max_total_mentions"), "max_total_mentions", 10
        )
        self._block_everyone: bool = self.config.get("block_everyone", False)
        self._count_unique_only: bool = self.config.get("count_unique_only", False)

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        """Check for mention spam."""
        # Match on a canonical view of the text. Invisible format/control
        # characters (zero-width space/joiner, soft hyphen, bidi override, BOM,
        # ...) spliced *inside* a token were silently skipped by the patterns
        # below: ``<@123\u200b456>`` and ``@e\u200bveryone`` look like real
        # mentions to a reader but produced zero matches, so mass-mention and
        # @everyone abuse sailed through this rule. Every sibling text rule
        # (repeated, emoji, keyword, spam, links) already folds these away;
        # this rule was the remaining normalization gap. NFKC folding also
        # maps compatibility forms (fullwidth digits) onto the ASCII shapes
        # the mention patterns understand. Length is bounded because NFKC can
        # expand a single codepoint into many.
        content = fold_invisible_for_matching(content)

        user_mentions = self.USER_MENTION_PATTERN.findall(content)
        role_mentions = self.ROLE_MENTION_PATTERN.findall(content)
        everyone_mentions = self.EVERYONE_PATTERN.findall(content)

        if self._count_unique_only:
            user_mentions = list(set(user_mentions))
            role_mentions = list(set(role_mentions))

        user_count = len(user_mentions)
        role_count = len(role_mentions)
        everyone_count = len(everyone_mentions)
        # @everyone/@here always count toward the total-mention limit;
        # block_everyone is a separate policy that forbids them outright.
        total_count = user_count + role_count + everyone_count

        violations = []
        highest_severity = ViolationSeverity.LOW

        if user_count > self._max_user_mentions:
            violations.append(
                f"{user_count} user mentions (max {self._max_user_mentions})"
            )
            highest_severity = ViolationSeverity.MEDIUM

        if role_count > self._max_role_mentions:
            violations.append(
                f"{role_count} role mentions (max {self._max_role_mentions})"
            )
            highest_severity = ViolationSeverity.MEDIUM

        if total_count > self._max_total_mentions:
            violations.append(
                f"{total_count} total mentions (max {self._max_total_mentions})"
            )
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
                "violations": violations,
            },
            severity=highest_severity,
        )

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate mention spam rule configuration."""
        issues = []

        limit_fields = ["max_user_mentions", "max_role_mentions", "max_total_mentions"]
        if not any(field in config for field in limit_fields):
            issues.append(
                "at least one of max_user_mentions, max_role_mentions, "
                "max_total_mentions is required"
            )

        for field in limit_fields:
            value = config.get(field)
            if value is not None:
                try:
                    bounded_int(value, field, minimum=0, maximum=10_000)
                except HardeningViolation:
                    issues.append(f"{field} must be an integer between 0 and 10000")

        if "block_everyone" in config and not isinstance(
            config["block_everyone"], bool
        ):
            issues.append("block_everyone must be a boolean")

        if "count_unique_only" in config and not isinstance(
            config["count_unique_only"], bool
        ):
            issues.append("count_unique_only must be a boolean")

        return len(issues) == 0, issues
