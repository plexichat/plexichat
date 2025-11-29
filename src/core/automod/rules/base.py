"""
Base rule class - Abstract base for all automod rule types.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from ..models import Rule, ViolationSeverity


@dataclass
class RuleMatch:
    """Result of a rule check."""
    matched: bool
    severity: ViolationSeverity = ViolationSeverity.MEDIUM
    matched_content: Optional[str] = None
    trigger_details: Dict[str, Any] = field(default_factory=dict)


class BaseRule(ABC):
    """Abstract base class for all automod rules."""
    
    def __init__(self, rule: Rule):
        """
        Initialize the rule.
        
        Args:
            rule: Rule configuration from database
        """
        self.rule = rule
        self.config = rule.trigger_config
    
    @abstractmethod
    def check(self, content: str, context: Dict[str, Any]) -> RuleMatch:
        """
        Check content against this rule.
        
        Args:
            content: Message content to check
            context: Additional context (user_id, channel_id, server_id, etc.)
            
        Returns:
            RuleMatch with result
        """
        pass
    
    @classmethod
    @abstractmethod
    def validate_config(cls, config: Dict[str, Any]) -> List[str]:
        """
        Validate rule configuration.
        
        Args:
            config: Configuration dictionary to validate
            
        Returns:
            List of validation error messages (empty if valid)
        """
        pass
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """
        Get default configuration for this rule type.
        
        Returns:
            Default configuration dictionary
        """
        return {}
    
    def is_exempt(self, user_roles: List[int], channel_id: int) -> bool:
        """
        Check if user/channel is exempt from this rule.
        
        Args:
            user_roles: List of role IDs the user has
            channel_id: Channel ID where message was sent
            
        Returns:
            True if exempt
        """
        if channel_id in self.rule.exempt_channels:
            return True
        
        for role_id in user_roles:
            if role_id in self.rule.exempt_roles:
                return True
        
        return False
    
    def _calculate_severity(self, match_count: int = 1, thresholds: Dict[str, int] = None) -> ViolationSeverity:
        """
        Calculate severity based on match count and thresholds.
        
        Args:
            match_count: Number of matches found
            thresholds: Optional custom thresholds
            
        Returns:
            Calculated severity level
        """
        if thresholds is None:
            thresholds = {
                "critical": 10,
                "high": 5,
                "medium": 2,
                "low": 1,
            }
        
        if match_count >= thresholds.get("critical", 10):
            return ViolationSeverity.CRITICAL
        elif match_count >= thresholds.get("high", 5):
            return ViolationSeverity.HIGH
        elif match_count >= thresholds.get("medium", 2):
            return ViolationSeverity.MEDIUM
        else:
            return ViolationSeverity.LOW
