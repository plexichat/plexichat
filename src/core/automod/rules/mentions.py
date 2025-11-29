"""
Mention spam rule - Detects excessive mentions in messages.
"""

import re
from typing import Dict, Any, List

from .base import BaseRule, RuleMatch
from ..models import Rule, ViolationSeverity


class MentionSpamRule(BaseRule):
    """Rule that detects excessive mentions in messages."""
    
    USER_MENTION_PATTERN = re.compile(r'<@!?(\d+)>')
    ROLE_MENTION_PATTERN = re.compile(r'<@&(\d+)>')
    EVERYONE_PATTERN = re.compile(r'@(everyone|here)\b')
    
    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_user_mentions = self.config.get("max_user_mentions", 5)
        self._max_role_mentions = self.config.get("max_role_mentions", 3)
        self._max_total_mentions = self.config.get("max_total_mentions", 10)
        self._block_everyone = self.config.get("block_everyone", True)
        self._count_unique_only = self.config.get("count_unique_only", False)
    
    def check(self, content: str, context: Dict[str, Any]) -> RuleMatch:
        """Check for mention spam."""
        user_mentions = self.USER_MENTION_PATTERN.findall(content)
        role_mentions = self.ROLE_MENTION_PATTERN.findall(content)
        everyone_mentions = self.EVERYONE_PATTERN.findall(content)
        
        if self._count_unique_only:
            user_mentions = list(set(user_mentions))
            role_mentions = list(set(role_mentions))
        
        user_count = len(user_mentions)
        role_count = len(role_mentions)
        total_count = user_count + role_count
        has_everyone = len(everyone_mentions) > 0
        
        violations = []
        
        if user_count > self._max_user_mentions:
            violations.append(f"user_mentions:{user_count}")
        
        if role_count > self._max_role_mentions:
            violations.append(f"role_mentions:{role_count}")
        
        if total_count > self._max_total_mentions:
            violations.append(f"total_mentions:{total_count}")
        
        if has_everyone and self._block_everyone:
            violations.append("everyone_mention")
        
        if not violations:
            return RuleMatch(matched=False)
        
        severity = self._calculate_mention_severity(user_count, role_count, has_everyone)
        
        return RuleMatch(
            matched=True,
            severity=severity,
            matched_content=f"{total_count} mentions",
            trigger_details={
                "user_mentions": user_count,
                "role_mentions": role_count,
                "total_mentions": total_count,
                "has_everyone": has_everyone,
                "violations": violations,
            }
        )
    
    def _calculate_mention_severity(self, user_count: int, role_count: int, has_everyone: bool) -> ViolationSeverity:
        """Calculate severity based on mention counts."""
        if has_everyone:
            return ViolationSeverity.HIGH
        
        total = user_count + role_count
        
        if total >= 20 or role_count >= 10:
            return ViolationSeverity.CRITICAL
        elif total >= 10 or role_count >= 5:
            return ViolationSeverity.HIGH
        elif total >= 5:
            return ViolationSeverity.MEDIUM
        else:
            return ViolationSeverity.LOW
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> List[str]:
        """Validate mention spam rule configuration."""
        issues = []
        
        for key in ["max_user_mentions", "max_role_mentions", "max_total_mentions"]:
            value = config.get(key)
            if value is not None:
                if not isinstance(value, int) or value < 0:
                    issues.append(f"{key} must be a non-negative integer")
                elif value > 100:
                    issues.append(f"{key} cannot exceed 100")
        
        if "block_everyone" in config and not isinstance(config["block_everyone"], bool):
            issues.append("block_everyone must be a boolean")
        
        if "count_unique_only" in config and not isinstance(config["count_unique_only"], bool):
            issues.append("count_unique_only must be a boolean")
        
        return issues
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "max_user_mentions": 5,
            "max_role_mentions": 3,
            "max_total_mentions": 10,
            "block_everyone": True,
            "count_unique_only": False,
        }
