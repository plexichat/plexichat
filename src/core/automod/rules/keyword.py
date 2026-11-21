"""
Keyword filter rule.

Checks messages for blocked keywords with optional word boundary matching.
"""

from typing import Dict, Any, Optional, List

from .base import BaseRule
from ..canonicalization import canonicalize_for_matching
from ..limits import (
    MAX_KEYWORD_LENGTH,
    MAX_KEYWORDS,
    MAX_SEVERITY_MAP_ENTRIES,
)
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity, coerce_severity


class KeywordRule(BaseRule):
    """Rule that checks for blocked keywords."""

    rule_type = RuleType.KEYWORD

    def __init__(self, rule: Rule):
        super().__init__(rule)
        # Stored configs bypass validate_config(). Bound the list size and
        # each entry so one rule cannot load thousands of keywords into every
        # worker or scan with a megabyte-sized keyword.
        raw_keywords = self.config.get("keywords", [])
        if not isinstance(raw_keywords, list):
            raw_keywords = []
        self._case_sensitive: bool = self.config.get("case_sensitive", False)
        self._whole_word: bool = self.config.get("whole_word", True)
        self._keywords: List[str] = [
            k.strip()
            for k in raw_keywords
            if isinstance(k, str) and k.strip() and len(k) <= MAX_KEYWORD_LENGTH
        ][:MAX_KEYWORDS]
        # Keep the display strings above for matched_content / severity-map
        # lookups, but compare the canonicalized bytes. Without this, splicing
        # a zero-width joiner or using fullwidth forms into a blocked word
        # evades the filter while reading identically to a human.
        self._canonical_keywords: List[str] = []
        display_keywords: List[str] = []
        for keyword in self._keywords:
            canonical = canonicalize_for_matching(
                keyword, case_sensitive=self._case_sensitive
            )
            # A keyword made solely of invisible characters folds to nothing;
            # skip it rather than let an empty needle match every message.
            if canonical:
                display_keywords.append(keyword)
                self._canonical_keywords.append(canonical)
        self._keywords = display_keywords
        raw_map = self.config.get("severity_map", {})
        if not isinstance(raw_map, dict):
            raw_map = {}
        self._severity_map: Dict[str, str] = {
            str(k)[:MAX_KEYWORD_LENGTH]: str(v)[:32] for k, v in raw_map.items()
        }
        if len(self._severity_map) > MAX_SEVERITY_MAP_ENTRIES:
            self._severity_map = dict(
                list(self._severity_map.items())[:MAX_SEVERITY_MAP_ENTRIES]
            )

        if not self._case_sensitive:
            self._keywords = [k.lower() for k in self._keywords]
            self._severity_map = {k.lower(): v for k, v in self._severity_map.items()}

    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuleMatch:
        """Check content for blocked keywords."""
        if not self._keywords:
            return self._no_match()

        # Filter on the FULL canonical view, not the truncated one. The raw
        # message carrying the blocked word is what the send path stores,
        # renders to every recipient and copies into notification previews;
        # the default MAX_CANONICAL_LENGTH cap can be blown past by NFKC
        # expansion (U+FDFA -> 18 codepoints) with a few hundred schema-legal
        # characters. Truncating here would hide a blocked word placed after
        # the cut from this rule while it is delivered verbatim to users.
        # Message content is already length-bounded at the API schema, so
        # scanning the whole folded form stays linear and bounded. This keeps
        # the filter view identical to the consumer's view (strictest wins).
        check_content = canonicalize_for_matching(
            content,
            case_sensitive=self._case_sensitive,
            max_length=None,
        )

        matched_keywords = []
        highest_severity = ViolationSeverity.LOW

        for keyword, canonical in zip(self._keywords, self._canonical_keywords):
            if self._whole_word:
                if self._contains_whole_word(check_content, canonical):
                    matched_keywords.append(keyword)
            else:
                if canonical in check_content:
                    matched_keywords.append(keyword)

        if not matched_keywords:
            return self._no_match()

        for kw in matched_keywords:
            sev_str = self._severity_map.get(kw, "medium")
            sev = self._parse_severity(sev_str)
            if self._severity_rank(sev) > self._severity_rank(highest_severity):
                highest_severity = sev

        return self._create_match(
            matched=True,
            matched_content=", ".join(matched_keywords),
            details={"keywords": matched_keywords, "count": len(matched_keywords)},
            severity=highest_severity,
        )

    def _contains_whole_word(self, text: str, word: str) -> bool:
        """Check if text contains word as a whole word."""
        word_chars = set(
            "abcdefghijklmnopqrstuvwxyz" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "0123456789_"
        )

        start = 0
        while True:
            pos = text.find(word, start)
            if pos == -1:
                return False

            before_ok = pos == 0 or text[pos - 1] not in word_chars
            after_pos = pos + len(word)
            after_ok = after_pos >= len(text) or text[after_pos] not in word_chars

            if before_ok and after_ok:
                return True

            start = pos + 1

    def _parse_severity(self, sev_str: Any) -> ViolationSeverity:
        """Parse a severity string through the shared allowlist."""
        try:
            return coerce_severity(sev_str, ViolationSeverity.MEDIUM)
        except ValueError:
            return ViolationSeverity.MEDIUM

    def _severity_rank(self, sev: ViolationSeverity) -> int:
        """Get numeric rank for severity comparison."""
        ranks = {
            ViolationSeverity.LOW: 1,
            ViolationSeverity.MEDIUM: 2,
            ViolationSeverity.HIGH: 3,
            ViolationSeverity.CRITICAL: 4,
        }
        return ranks.get(sev, 2)

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate keyword rule configuration."""
        issues = []

        keywords = config.get("keywords")
        if not keywords:
            issues.append("keywords list is required")
        elif not isinstance(keywords, list):
            issues.append("keywords must be a list")
        elif not all(isinstance(k, str) for k in keywords):
            issues.append("all keywords must be strings")
        elif len(keywords) > MAX_KEYWORDS:
            issues.append(f"keywords list exceeds {MAX_KEYWORDS} entries")
        elif any(not k.strip() or len(k) > MAX_KEYWORD_LENGTH for k in keywords):
            issues.append(
                f"each keyword must be non-empty and at most {MAX_KEYWORD_LENGTH} characters"
            )

        severity_map = config.get("severity_map", {})
        if severity_map is not None:
            if not isinstance(severity_map, dict):
                issues.append("severity_map must be an object")
            elif len(severity_map) > MAX_SEVERITY_MAP_ENTRIES:
                issues.append(
                    f"severity_map exceeds {MAX_SEVERITY_MAP_ENTRIES} entries"
                )
            elif any(
                not isinstance(k, str)
                or len(k) > MAX_KEYWORD_LENGTH
                or not isinstance(v, str)
                or len(v) > 32
                for k, v in severity_map.items()
            ):
                issues.append("severity_map keys/values are too long")

        if "case_sensitive" in config and not isinstance(
            config["case_sensitive"], bool
        ):
            issues.append("case_sensitive must be a boolean")

        if "whole_word" in config and not isinstance(config["whole_word"], bool):
            issues.append("whole_word must be a boolean")

        return len(issues) == 0, issues
