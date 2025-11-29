"""
Mass emoji detection rule.

Detects messages with excessive emoji usage.
"""

import re
from typing import Dict, Any, Optional

from .base import BaseRule
from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


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
        self._max_emoji: int = self.config.get("max_emoji", 10)
        self._max_percentage: float = self.config.get("max_percentage", 50.0)
        self._count_custom: bool = self.config.get("count_custom", True)
        self._count_unicode: bool = self.config.get("count_unicode", True)
        self._min_length: int = self.config.get("min_length", 5)
    
    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None
    ) -> RuleMatch:
        """Check for excessive emoji usage."""
        custom_count = 0
        unicode_count = 0
        
        if self._count_custom:
            custom_matches = self.CUSTOM_EMOJI_PATTERN.findall(content)
            custom_count = len(custom_matches)
        
        if self._count_unicode:
            unicode_matches = self.UNICODE_EMOJI_PATTERN.findall(content)
            unicode_count = sum(len(m) for m in unicode_matches)
        
        total_emoji = custom_count + unicode_count
        
        if total_emoji == 0:
            return self._no_match()
        
        content_without_emoji = self.CUSTOM_EMOJI_PATTERN.sub("", content)
        content_without_emoji = self.UNICODE_EMOJI_PATTERN.sub("", content_without_emoji)
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
                violations.append(f"{emoji_percentage:.1f}% emoji (max {self._max_percentage}%)")
        
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
                "violations": violations
            },
            severity=ViolationSeverity.LOW
        )
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """Validate mass emoji rule configuration."""
        issues = []
        
        max_emoji = config.get("max_emoji", 10)
        if not isinstance(max_emoji, int) or max_emoji < 1:
            issues.append("max_emoji must be a positive integer")
        
        max_pct = config.get("max_percentage", 50.0)
        if not isinstance(max_pct, (int, float)) or not 0 <= max_pct <= 100:
            issues.append("max_percentage must be between 0 and 100")
        
        for field in ["count_custom", "count_unicode"]:
            if field in config and not isinstance(config[field], bool):
                issues.append(f"{field} must be a boolean")
        
        return len(issues) == 0, issues
