"""
Message spam detection rule.

Detects rapid message sending (rate limiting) and duplicate content spam.
"""

import time
from typing import Dict, Any, Optional

from src.core.security.hardening import HardeningViolation, bounded_int

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


def _bounded(value, field, minimum, maximum, default):
    """Coerce a stored config integer to a finite, sane range."""
    try:
        return bounded_int(
            value, field, minimum=minimum, maximum=maximum, default=default
        )
    except HardeningViolation:
        return default


class MessageSpamRule(BaseRule):
    """Rule that detects message spam based on rate and content."""

    rule_type = RuleType.MESSAGE_SPAM

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_messages: int = _bounded(
            self.config.get("max_messages"), "max_messages", 1, 10_000, 5
        )
        self._window_seconds: int = _bounded(
            self.config.get("window_seconds"), "window_seconds", 1, 86_400, 5
        )
        self._duplicate_threshold: int = _bounded(
            self.config.get("duplicate_threshold"), "duplicate_threshold", 1, 10_000, 3
        )
        self._duplicate_window: int = _bounded(
            self.config.get("duplicate_window_seconds"),
            "duplicate_window_seconds",
            1,
            604_800,
            60,
        )
        similarity = self.config.get("similarity_threshold", 0.9)
        self._similarity_threshold: float = (
            float(similarity)
            if isinstance(similarity, (int, float))
            and not isinstance(similarity, bool)
            and 0 <= similarity <= 1
            else 0.9
        )

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        """Check for message spam."""
        context = context or {}

        rate_violation = self._check_rate_spam(user_id, context)
        if rate_violation:
            return rate_violation

        duplicate_violation = self._check_duplicate_spam(content, user_id, context)
        if duplicate_violation:
            return duplicate_violation

        return self._no_match()

    def _check_rate_spam(
        self, user_id: int, context: Dict[str, Any]
    ) -> Optional[RuleMatch]:
        """Check if user is sending messages too fast."""
        recent_messages = context.get("recent_messages", [])
        if not recent_messages:
            return None

        now = int(time.time() * 1000)
        window_start = now - (self._window_seconds * 1000)

        messages_in_window = [
            m
            for m in recent_messages
            if m.get("user_id") == user_id and m.get("created_at", 0) >= window_start
        ]

        if len(messages_in_window) >= self._max_messages:
            return self._create_match(
                matched=True,
                matched_content=f"{len(messages_in_window)} messages in {self._window_seconds}s",
                details={
                    "type": "rate_spam",
                    "message_count": len(messages_in_window),
                    "window_seconds": self._window_seconds,
                    "threshold": self._max_messages,
                },
                severity=ViolationSeverity.MEDIUM,
            )

        return None

    def _check_duplicate_spam(
        self, content: str, user_id: int, context: Dict[str, Any]
    ) -> Optional[RuleMatch]:
        """Check for duplicate message content."""
        recent_messages = context.get("recent_messages", [])
        if not recent_messages:
            return None

        now = int(time.time() * 1000)
        window_start = now - (self._duplicate_window * 1000)

        content_lower = content.lower().strip()
        duplicate_count = 0

        for msg in recent_messages:
            if msg.get("user_id") != user_id:
                continue
            if msg.get("created_at", 0) < window_start:
                continue

            msg_content = msg.get("content", "").lower().strip()

            if msg_content == content_lower:
                duplicate_count += 1
            elif (
                self._calculate_similarity(content_lower, msg_content)
                >= self._similarity_threshold
            ):
                duplicate_count += 1

        if duplicate_count >= self._duplicate_threshold:
            return self._create_match(
                matched=True,
                matched_content=f"{duplicate_count} duplicate messages",
                details={
                    "type": "duplicate_spam",
                    "duplicate_count": duplicate_count,
                    "threshold": self._duplicate_threshold,
                },
                severity=ViolationSeverity.MEDIUM,
            )

        return None

    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """Calculate word-level Jaccard similarity between two strings."""
        if not s1 or not s2:
            return 0.0

        if s1 == s2:
            return 1.0

        words1 = set(s1.lower().split())
        words2 = set(s2.lower().split())
        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union)

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate spam rule configuration."""
        issues = []

        if "max_messages" not in config:
            issues.append("max_messages is required")

        for field, minimum, maximum, default in (
            ("max_messages", 1, 10_000, 5),
            ("window_seconds", 1, 86_400, 5),
            ("duplicate_threshold", 1, 10_000, 3),
            ("duplicate_window_seconds", 1, 604_800, 60),
        ):
            try:
                bounded_int(
                    config.get(field, default),
                    field,
                    minimum=minimum,
                    maximum=maximum,
                )
            except HardeningViolation:
                issues.append(
                    f"{field} must be an integer between {minimum} and {maximum}"
                )

        similarity = config.get("similarity_threshold", 0.9)
        if not isinstance(similarity, (int, float)) or not 0 <= similarity <= 1:
            issues.append("similarity_threshold must be between 0 and 1")

        return len(issues) == 0, issues
