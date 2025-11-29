"""
Delete message action - Deletes the violating message.
"""

from typing import Dict, Any

import utils.logger as logger

from .base import BaseAction, ActionResult
from ..models import RuleAction, Violation, ActionType


class DeleteMessageAction(BaseAction):
    """Action that deletes the violating message."""
    
    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Dict[str, Any]
    ) -> ActionResult:
        """Execute message deletion."""
        message_id = violation.message_id
        
        if not message_id:
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error="No message ID provided"
            )
        
        db = context.get("db")
        if not db:
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error="Database not available"
            )
        
        try:
            import time
            now = int(time.time() * 1000)
            
            db.execute(
                "UPDATE msg_messages SET deleted = 1, deleted_at = ? WHERE id = ?",
                (now, message_id)
            )
            
            logger.debug(f"AutoMod deleted message {message_id} for violation {violation.id}")
            
            if action.notify_user:
                self._notify_user(
                    violation.user_id,
                    violation.server_id,
                    f"Your message was removed by AutoMod: {action.reason or 'Rule violation'}",
                    context
                )
            
            return ActionResult(
                success=True,
                action_type=self.get_action_type(),
                message=f"Deleted message {message_id}",
                metadata={"message_id": message_id}
            )
            
        except Exception as e:
            logger.error(f"Failed to delete message {message_id}: {e}")
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error=str(e)
            )
    
    @classmethod
    def get_action_type(cls) -> str:
        return ActionType.DELETE_MESSAGE.value
