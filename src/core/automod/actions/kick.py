"""
Kick user action - Removes user from the server.
"""

import time
from typing import Dict, Any

import utils.logger as logger

from .base import BaseAction, ActionResult
from ..models import RuleAction, Violation, ActionType


class KickUserAction(BaseAction):
    """Action that kicks a user from the server."""
    
    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Dict[str, Any]
    ) -> ActionResult:
        """Execute user kick."""
        user_id = violation.user_id
        server_id = violation.server_id
        
        db = context.get("db")
        if not db:
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error="Database not available"
            )
        
        try:
            member = db.fetch_one(
                "SELECT id FROM srv_members WHERE server_id = ? AND user_id = ?",
                (server_id, user_id)
            )
            
            if not member:
                return ActionResult(
                    success=False,
                    action_type=self.get_action_type(),
                    error="User is not a member of this server"
                )
            
            server = db.fetch_one(
                "SELECT owner_id FROM srv_servers WHERE id = ?",
                (server_id,)
            )
            
            if server and server["owner_id"] == user_id:
                return ActionResult(
                    success=False,
                    action_type=self.get_action_type(),
                    error="Cannot kick server owner"
                )
            
            db.execute(
                "DELETE FROM srv_member_roles WHERE server_id = ? AND user_id = ?",
                (server_id, user_id)
            )
            
            db.execute(
                "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
                (server_id, user_id)
            )
            
            now = int(time.time() * 1000)
            from src.utils.encryption import generate_snowflake_id
            audit_id = generate_snowflake_id()
            
            db.execute(
                """INSERT INTO srv_audit_log 
                   (id, server_id, action_type, user_id, target_id, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (audit_id, server_id, "MEMBER_KICK", 0, user_id, 
                 f"AutoMod: {action.reason or 'Rule violation'}", now)
            )
            
            logger.debug(f"AutoMod kicked user {user_id} from server {server_id}")
            
            if action.notify_user:
                self._notify_user(
                    user_id,
                    server_id,
                    f"You have been kicked from the server: {action.reason or 'Rule violation'}",
                    context
                )
            
            return ActionResult(
                success=True,
                action_type=self.get_action_type(),
                message=f"Kicked user {user_id} from server {server_id}",
                metadata={"user_id": user_id, "server_id": server_id}
            )
            
        except Exception as e:
            logger.error(f"Failed to kick user {user_id}: {e}")
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error=str(e)
            )
    
    @classmethod
    def get_action_type(cls) -> str:
        return ActionType.KICK_USER.value
