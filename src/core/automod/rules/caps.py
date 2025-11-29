"""
Caps percentage rule - Detects excessive use of capital letters.
"""

from typing import Dict, Any, List

from .base import BaseRule, RuleMatch
from ..models import Rule, ViolationSeverity


class CapsPercentageRule(BaseRule):
    """Rule that detects excessive capital letter usage."""
    
    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_caps_percentage = self.config.get("max_caps_percentage", 70)
        self._min_message_length = self.config.get("min_message_length", 10)
        self._ignore_urls = self.config.get("ignore_urls", True)
        self._ignore_mentions = self.config.get("ignore_mentions", True)
    
    def check(self, content: str, context: Dict[str, Any]) -> RuleMatch:
        """Check for excessive caps."""
        check_content = self._prepare_content(content)
        
        if len(check_content) < self._min_message_length:
            return RuleMatch(matched=False)
        
        letter_count = sum(1 for c in check_content if c.isalpha())
        
        if letter_count == 0:
            return RuleMatch(matched=False)
        
        caps_count = sum(1 for c in check_content if c.isupper())
        caps_percentage = (caps_count / letter_count) * 100
        
        if caps_percentage <= self._max_caps_percentage:
            return RuleMatch(matched=False)
        
        severity = self._calculate_caps_severity(caps_percentage)
        
        return RuleMatch(
            matched=True,
            severity=severity,
            matched_content=f"{caps_percentage:.1f}% caps",
            trigger_details={
                "caps_percentage": round(caps_percentage, 2),
                "caps_count": caps_count,
                "letter_count": letter_count,
                "threshold": self._max_caps_percentage,
            }
        )
    
    def _prepare_content(self, content: str) -> str:
        """Prepare content for analysis by removing ignored elements."""
        import re
        
        result = content
        
        if self._ignore_urls:
            result = re.sub(r'https?://[^\s]+', '', result)
        
        if self._ignore_mentions:
            result = re.sub(r'<@!?\d+>', '', result)
            result = re.sub(r'<@&\d+>', '', result)
            result = re.sub(r'<#\d+>', '', result)
        
        return result
    
    def _calculate_caps_severity(self, percentage: float) -> ViolationSeverity:
        """Calculate severity based on caps percentage."""
        if percentage >= 95:
            return ViolationSeverity.HIGH
        elif percentage >= 85:
            return ViolationSeverity.MEDIUM
        else:
            return ViolationSeverity.LOW
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> List[str]:
        """Validate caps percentage rule configuration."""
        issues = []
        
        max_caps = config.get("max_caps_percentage", 70)
        if not isinstance(max_caps, (int, float)):
            issues.append("max_caps_percentage must be a number")
        elif max_caps < 0 or max_caps > 100:
            issues.append("max_caps_percentage must be between 0 and 100")
        
        min_length = config.get("min_message_length", 10)
        if not isinstance(min_length, int) or min_length < 1:
            issues.append("min_message_length must be a positive integer")
        elif min_length > 1000:
            issues.append("min_message_length cannot exceed 1000")
        
        for key in ["ignore_urls", "ignore_mentions"]:
            if key in config and not isinstance(config[key], bool):
                issues.append(f"{key} must be a boolean")
        
        return issues
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "max_caps_percentage": 70,
            "min_message_length": 10,
            "ignore_urls": True,
            "ignore_mentions": True,
        }
