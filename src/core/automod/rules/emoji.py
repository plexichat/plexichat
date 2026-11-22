"""
Mass emoji detection rule.

Detects messages with excessive emoji usage.
"""

import math
import re
from typing import Dict, Any, Optional

from src.core.security.hardening import HardeningViolation, bounded_int

from .base import BaseRule
from ..canonicalization import fold_invisible_for_matching
from ..limits import MAX_EMOJI_COUNT, MAX_MIN_LENGTH
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


def _bounded(value, field, minimum, maximum, default):
    try:
        return bounded_int(
            value, field, minimum=minimum, maximum=maximum, default=default
        )
    except HardeningViolation:
        return default


def _clamped_int(value, minimum, maximum, default):
    """Clamp a stored integer knob into range, falling back on non-integers.

    ``min_length`` is admin-stored but still untrusted input: a stored 1e9
    made the rule skip every realistic message, so an oversized value is
    pulled down to the shared ceiling rather than trusted or reset.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(minimum, min(value, maximum))


def _coerced_bool(value, default):
    """Accept only a real bool; stored strings keep the compiled default."""
    return value if isinstance(value, bool) else default


def _clamped_float(value, minimum, maximum, default):
    """Clamp a stored float knob into range, rejecting NaN/inf and bools.

    ``max_percentage: NaN`` used to make every ``percentage > NaN`` compare
    False and silently disable the percentage check for the whole rule.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if not math.isfinite(number):
        return default
    return max(minimum, min(number, maximum))


class MassEmojiRule(BaseRule):
    """Rule that detects excessive emoji usage."""

    rule_type = RuleType.MASS_EMOJI

    CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:\w+:\d+>")
    UNICODE_EMOJI_PATTERN = re.compile(
        r"[\U0001F600-\U0001F64F"
        r"\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF"
        r"\U0001F1E0-\U0001F1FF"
        r"\U00002702-\U000027B0"
        r"\U0001F900-\U0001F9FF"
        r"\U0001FA00-\U0001FA6F"
        r"\U0001FA70-\U0001FAFF"
        r"\U00002600-\U000026FF]+"
    )

    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_emoji: int = _bounded(
            self.config.get("max_emoji"), "max_emoji", 1, MAX_EMOJI_COUNT, 10
        )
        self._max_percentage: float = _clamped_float(
            self.config.get("max_percentage"), 0.0, 100.0, 50.0
        )
        # Stored configs are JSON that an admin may have edited by hand; a
        # truthy string used to make the rule treat the toggle as opaque, so
        # only a real bool is honoured and anything else keeps the default.
        self._count_custom: bool = _coerced_bool(self.config.get("count_custom"), True)
        self._count_unicode: bool = _coerced_bool(
            self.config.get("count_unicode"), True
        )
        self._min_length: int = _clamped_int(
            self.config.get("min_length"), 1, MAX_MIN_LENGTH, 5
        )

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        """Check for excessive emoji usage."""
        # Canonicalize before matching. Invisible separators between emoji used
        # to (a) split one emoji run into many and (b) pad ``text_length`` with
        # invisible characters, deflating the emoji percentage below the cap
        # even though a human sees a wall of emoji. NFKC folding also folds
        # compatibility-codepoint lookalikes onto the patterns below.
        #
        # Match on the FULL folded view, not the truncated one. NFKC can expand
        # ONE codepoint into many (U+FDFA -> 18), so a few hundred schema-legal
        # characters can push the folded form past MAX_CANONICAL_LENGTH; emoji
        # placed after the cut would be invisible here while the raw message
        # (with every emoji) is stored and rendered to recipients. That is the
        # same filter/consumer view gap fixed for keywords and mentions. Input
        # is length-bounded at the schema, so the full form stays bounded.
        content = fold_invisible_for_matching(content, max_length=None)

        custom_count = 0
        unicode_count = 0

        if self._count_custom:
            custom_matches = self.CUSTOM_EMOJI_PATTERN.findall(content)
            custom_count = len(custom_matches)

        if self._count_unicode:
            unicode_matches = self.UNICODE_EMOJI_PATTERN.findall(content)
            # The pattern is a character class with a ``+`` quantifier, so one
            # match is a *run* of adjacent emoji, not a single emoji. Counting
            # the matches (``len(unicode_matches)``) therefore counted "😀" * 20
            # as one emoji and let mass-emoji spam through; sum the code points
            # each match spans so every emoji is counted.
            unicode_count = sum(len(m) for m in unicode_matches)

        total_emoji = custom_count + unicode_count

        if total_emoji == 0:
            return self._no_match()

        content_without_emoji = self.CUSTOM_EMOJI_PATTERN.sub("", content)
        content_without_emoji = self.UNICODE_EMOJI_PATTERN.sub(
            "", content_without_emoji
        )
        text_length = len(content_without_emoji.strip())

        total_length = text_length + total_emoji
        if total_length < self._min_length:
            return self._no_match()

        violations = []

        if total_emoji > self._max_emoji:
            violations.append(f"{total_emoji} emoji (max {self._max_emoji})")

        if total_length > 0:
            emoji_percentage = (total_emoji / total_length) * 100
            if emoji_percentage > self._max_percentage:
                violations.append(
                    f"{emoji_percentage:.1f}% emoji (max {self._max_percentage}%)"
                )

        if not violations:
            return self._no_match()

        return self._create_match(
            matched=True,
            matched_content="; ".join(violations),
            details={
                "custom_emoji": custom_count,
                "unicode_emoji": unicode_count,
                "total_emoji": total_emoji,
                "text_length": text_length,
                "violations": violations,
            },
            severity=ViolationSeverity.LOW,
        )

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate mass emoji rule configuration."""
        issues = []

        try:
            bounded_int(
                config.get("max_emoji", 10),
                "max_emoji",
                minimum=1,
                maximum=MAX_EMOJI_COUNT,
            )
        except HardeningViolation:
            issues.append(
                f"max_emoji must be an integer between 1 and {MAX_EMOJI_COUNT}"
            )

        max_pct = config.get("max_percentage", 50.0)
        if (
            isinstance(max_pct, bool)
            or not isinstance(max_pct, (int, float))
            or not math.isfinite(max_pct)
            or not 0 <= max_pct <= 100
        ):
            issues.append("max_percentage must be between 0 and 100")

        min_len = config.get("min_length", 5)
        if (
            isinstance(min_len, bool)
            or not isinstance(min_len, int)
            or not 1 <= min_len <= MAX_MIN_LENGTH
        ):
            issues.append(
                f"min_length must be an integer between 1 and {MAX_MIN_LENGTH}"
            )

        for field in ["count_custom", "count_unicode"]:
            if field in config and not isinstance(config[field], bool):
                issues.append(f"{field} must be a boolean")

        return len(issues) == 0, issues
