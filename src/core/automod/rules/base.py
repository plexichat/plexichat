"""
Base rule class for automod rules.

All rule implementations inherit from BaseRule.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from ..models import Rule, RuleMatch, RuleType, ViolationSeverity


class BaseRule(ABC):
    """Abstract base class for all automod rules."""
    
    rule_type: RuleType = None
    
    def __init__(self, rule: Rule):
        """
        Initialize the rule.
        
        Args:
            rule: Rule configuration from database
        """
        self.rule = rule
        self.config = rule.config
    
    @abstractmethod
    def check(
        self,
        content: str,
        user_id: int,
        channel_id: int,
        context: Optional[Dict[str, Any]] = None
    ) -> RuleMatch:
        """
        Check content against this rule.
        
        Args:
            content: Message content to check
            user_id: ID of user who sent the message
            channel_id: ID of channel where message was sent
            context: Additional context (message history, etc.)
            
        Returns:
            RuleMatch with results
        """
        pass
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple:
        """
        Validate rule configuration.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Tuple of (valid: bool, issues: list)
        """
        return True, []
    
    def _create_match(
        self,
        matched: bool,
        matched_content: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        severity: ViolationSeverity = ViolationSeverity.MEDIUM
    ) -> RuleMatch:
        """Helper to create a RuleMatch."""
        return RuleMatch(
            rule_id=self.rule.id,
            rule_type=self.rule.rule_type,
            matched=matched,
            matched_content=matched_content,
            match_details=details or {},
            severity=severity
        )
    
    def _no_match(self) -> RuleMatch:
        """Helper to create a non-matching result."""
        return self._create_match(matched=False)
