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
        # Count on TWO views and take the maximum per bucket.
        #
        # The canonical view strips invisible format/control characters that
        # were spliced *inside* a token: ``<@123\u200b456>`` and
        # ``@e\u200bveryone`` look like real mentions to a reader but produce
        # zero matches on the raw text, so mass-mention and @everyone abuse can
        # otherwise sail through this rule. NFKC folding also maps
        # compatibility forms (fullwidth digits) onto the ASCII shapes the
        # patterns understand.
        #
        # The RAW view is equally necessary. Canonicalization truncates at
        # MAX_CANONICAL_LENGTH, and NFKC can expand ONE codepoint into many
        # (U+FDFA becomes 18 characters). A short, schema-legal message can
        # expand past the cap, so every mention after the expansion point is
        # cut out of the canonical view -- yet the notification fan-out parses
        # those mentions from the RAW content and still pings their targets.
        # Counting only the canonical view therefore lets a user bypass the
        # mass-mention limits. Both views are linear and already bounded
        # (message length / canonical cap), so this adds no unbounded work.
        folded = fold_invisible_for_matching(content)
        raw = (
            content
            if isinstance(content, str)
            else ("" if content is None else str(content))
        )

        def _counts(text: str):
            users = self.USER_MENTION_PATTERN.findall(text)
            roles = self.ROLE_MENTION_PATTERN.findall(text)
            everyone = self.EVERYONE_PATTERN.findall(text)
            if self._count_unique_only:
                users = set(users)
                roles = set(roles)
            return len(users), len(roles), len(everyone)

        raw_counts = _counts(raw)
        folded_counts = _counts(folded)
        user_count = max(raw_counts[0], folded_counts[0])
        role_count = max(raw_counts[1], folded_counts[1])
        everyone_count = max(raw_counts[2], folded_counts[2])
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
