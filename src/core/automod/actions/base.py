"""
Base action class - Abstract base for all automod action executors.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

from ..models import RuleAction, Violation


@dataclass
class ActionResult:
    """Result of executing an action."""
    success: bool
    action_type: str
    message: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseAction(ABC):
    """Abstract base class for all automod actions."""
    
    def __init__(self, servers_module=None, messaging_module=None, notifications_module=None):
        """
        Initialize the action executor.
        
        Args:
            servers_module: Servers module for server operations
            messaging_module: Messaging module for message operations
            notifications_module: Notifications module for alerts
        """
        self._servers = servers_module
        self._messaging = messaging_module
        self._notifications = notifications_module
    
    @abstractmethod
    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Dict[str, Any]
    ) -> ActionResult:
        """
        Execute the action.
        
        Args:
            action: Action configuration
            violation: The violation that triggered this action
            context: Additional context (db, user_id, server_id, etc.)
            
        Returns:
            ActionResult with execution status
        """
        pass
    
    @classmethod
    @abstractmethod
    def get_action_type(cls) -> str:
        """Get the action type identifier."""
        pass
    
    def _notify_user(self, user_id: int, server_id: int, message: str, context: Dict[str, Any]) -> bool:
        """
        Send notification to user about action taken.
        
        Args:
            user_id: User to notify
            server_id: Server where action occurred
            message: Notification message
            context: Additional context
            
        Returns:
            True if notification sent
        """
        return True
