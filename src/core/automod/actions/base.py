"""
Base action class for automod actions.

All action implementations inherit from BaseAction.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from ..models import ActionType, RuleAction, Violation


class BaseAction(ABC):
    """Abstract base class for all automod actions."""
    
    action_type: ActionType = None
    
    def __init__(self, db, servers_module=None, messaging_module=None, notifications_module=None):
        """
        Initialize the action executor.
        
        Args:
            db: Database instance
            servers_module: Servers module for kicks/bans
            messaging_module: Messaging module for message operations
            notifications_module: Notifications module for alerts
        """
        self._db = db
        self._servers = servers_module
        self._messaging = messaging_module
        self._notifications = notifications_module
    
    @abstractmethod
    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Execute the action.
        
        Args:
            action: Action configuration
            violation: Violation that triggered this action
            context: Additional context
            
        Returns:
            True if action was executed successfully
        """
        pass
    
    def can_execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """
        Check if action can be executed.
        
        Args:
            action: Action configuration
            violation: Violation that triggered this action
            context: Additional context
            
        Returns:
            Tuple of (can_execute: bool, reason: str or None)
        """
        return True, None
