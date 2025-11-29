"""
Delete message action.

Deletes the message that triggered the violation.
"""

from typing import Dict, Any, Optional

import utils.logger as logger

from .base import BaseAction
from ..models import ActionType, RuleAction, Violation


class DeleteMessageAction(BaseAction):
    """Action that deletes the offending message."""
    
    action_type = ActionType.DELETE_MESSAGE
    
    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Delete the message."""
        if not violation.message_id:
            logger.warning(f"Cannot delete message: no message_id in violation {violation.id}")
            return False
        
        if not self._messaging:
            logger.warning("Cannot delete message: messaging module not available")
            return False
        
        try:
            bot_user_id = context.get("bot_user_id") if context else None
            if not bot_user_id:
                self._db.execute(
                    "UPDATE msg_messages SET deleted = 1, deleted_at = ? WHERE id = ?",
                    (violation.created_at, violation.message_id)
                )
            else:
                self._messaging.delete_message(
                    user_id=bot_user_id,
                    message_id=violation.message_id
                )
            
            logger.debug(f"Deleted message {violation.message_id} for violation {violation.id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete message {violation.message_id}: {e}")
            return False
    
    def can_execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """Check if message can be deleted."""
        if not violation.message_id:
            return False, "No message ID available"
        
        return True, None
