"""
Mass emoji rule - Detects excessive emoji usage in messages.
"""

import re
from typing import Dict, Any, List

from .base import BaseRule, RuleMatch
from ..models import Rule, ViolationSeverity


class MassEmojiRule(BaseRule):
    """Rule that detects excessive emoji usage."""
    
    CUSTOM_EMOJI_PATTERN = re.compile(r'<a?:\w+:\d+>')
    
    UNICODE_EMOJI_PATTERN = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\U00002600-\U000026FF"
        "\U00002700-\U000027BF"
        "]+"
    )
    
    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_emojis = self.config.get("max_emojis", 10)
        self._max_emoji_percentage = self.config.get("max_emoji_percentage", 50)
        self._count_custom = self.config.get("count_custom", True)
        self._count_unicode = self.config.get("count_unicode", True)
    
    def check(self, content: str, context: Dict[str, Any]) -> RuleMatch:
        """Check for excessive emoji usage."""
        custom_count = 0
        unicode_count = 0
        
        if self._count_custom:
            custom_emojis = self.CUSTOM_EMOJI_PATTERN.findall(content)
            custom_count = len(custom_emojis)
        
        if self._count_unicode:
            unicode_matches = self.UNICODE_EMOJI_PATTERN.findall(content)
            unicode_count = sum(len(m) for m in unicode_matches)
        
        total_emojis = custom_count + unicode_count
        
        if total_emojis == 0:
            return RuleMatch(matched=False)
        
        content_length = len(content.strip())
        emoji_percentage = (total_emojis / max(content_length, 1)) * 100
        
        violations = []
        
        if total_emojis > self._max_emojis:
            violations.append(f"count:{total_emojis}")
        
        if emoji_percentage > self._max_emoji_percentage:
            violations.append(f"percentage:{emoji_percentage:.1f}")
        
        if not violations:
            return RuleMatch(matched=False)
        
        severity = self._calculate_emoji_severity(total_emojis, emoji_percentage)
        
        return RuleMatch(
            matched=True,
            severity=severity,
            matched_content=f"{total_emojis} emojis ({emoji_percentage:.1f}%)",
            trigger_details={
                "total_emojis": total_emojis,
                "custom_emojis": custom_count,
                "unicode_emojis": unicode_count,
                "emoji_percentage": round(emoji_percentage, 2),
                "violations": violations,
            }
        )
    
    def _calculate_emoji_severity(self, count: int, percentage: float) -> ViolationSeverity:
        """Calculate severity based on emoji usage."""
        if count >= 50 or percentage >= 90:
            return ViolationSeverity.HIGH
        elif count >= 25 or percentage >= 70:
            return ViolationSeverity.MEDIUM
        else:
            return ViolationSeverity.LOW
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> List[str]:
        """Validate mass emoji rule configuration."""
        issues = []
        
        max_emojis = config.get("max_emojis", 10)
        if not isinstance(max_emojis, int) or max_emojis < 1:
            issues.append("max_emojis must be a positive integer")
        elif max_emojis > 500:
            issues.append("max_emojis cannot exceed 500")
        
        max_percentage = config.get("max_emoji_percentage", 50)
        if not isinstance(max_percentage, (int, float)):
            issues.append("max_emoji_percentage must be a number")
        elif max_percentage < 0 or max_percentage > 100:
            issues.append("max_emoji_percentage must be between 0 and 100")
        
        for key in ["count_custom", "count_unicode"]:
            if key in config and not isinstance(config[key], bool):
                issues.append(f"{key} must be a boolean")
        
        return issues
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "max_emojis": 10,
            "max_emoji_percentage": 50,
            "count_custom": True,
            "count_unicode": True,
        }
