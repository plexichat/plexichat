"""
Timeout user action - Temporarily restricts user from sending messages.
"""

import time
from typing import Dict, Any

import utils.logger as logger

from .base import BaseAction, ActionResult
from ..models import RuleAction, Violation, ActionType


class TimeoutUserAction(BaseAction):
    """Action that times out a user."""
    
    DEFAULT_DURATION = 300
    
    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Dict[str, Any]
    ) -> ActionResult:
        """Execute user timeout."""
        user_id = violation.user_id
        server_id = violation.server_id
        
        db = context.get("db")
        if not db:
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error="Database not available"
            )
        
        duration = action.duration_seconds or self.DEFAULT_DURATION
        
        try:
            now = int(time.time() * 1000)
            timeout_until = now + (duration * 1000)
            
            existing = db.fetch_one(
                "SELECT id FROM srv_member_timeouts WHERE server_id = ? AND user_id = ?",
                (server_id, user_id)
            )
            
            if existing:
                db.execute(
                    """UPDATE srv_member_timeouts 
                       SET timeout_until = ?, reason = ?, updated_at = ?
                       WHERE server_id = ? AND user_id = ?""",
                    (timeout_until, action.reason or "AutoMod violation", now, server_id, user_id)
                )
            else:
                from src.utils.encryption import generate_snowflake_id
                timeout_id = generate_snowflake_id()
                
                db.execute(
                    """INSERT INTO srv_member_timeouts 
                       (id, server_id, user_id, timeout_until, reason, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (timeout_id, server_id, user_id, timeout_until, action.reason or "AutoMod violation", now, now)
                )
            
            logger.debug(f"AutoMod timed out user {user_id} in server {server_id} for {duration}s")
            
            if action.notify_user:
                self._notify_user(
                    user_id,
                    server_id,
                    f"You have been timed out for {duration} seconds: {action.reason or 'Rule violation'}",
                    context
                )
            
            return ActionResult(
                success=True,
                action_type=self.get_action_type(),
                message=f"Timed out user {user_id} for {duration} seconds",
                metadata={
                    "user_id": user_id,
                    "duration_seconds": duration,
                    "timeout_until": timeout_until,
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to timeout user {user_id}: {e}")
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error=str(e)
            )
    
    @classmethod
    def get_action_type(cls) -> str:
        return ActionType.TIMEOUT_USER.value
