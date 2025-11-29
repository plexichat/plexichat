"""
Ban user action - Permanently bans user from the server.
"""

import time
from typing import Dict, Any

import utils.logger as logger

from .base import BaseAction, ActionResult
from ..models import RuleAction, Violation, ActionType


class BanUserAction(BaseAction):
    """Action that bans a user from the server."""
    
    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Dict[str, Any]
    ) -> ActionResult:
        """Execute user ban."""
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
            server = db.fetch_one(
                "SELECT owner_id FROM srv_servers WHERE id = ?",
                (server_id,)
            )
            
            if server and server["owner_id"] == user_id:
                return ActionResult(
                    success=False,
                    action_type=self.get_action_type(),
                    error="Cannot ban server owner"
                )
            
            existing_ban = db.fetch_one(
                "SELECT id FROM srv_bans WHERE server_id = ? AND user_id = ?",
                (server_id, user_id)
            )
            
            if existing_ban:
                return ActionResult(
                    success=True,
                    action_type=self.get_action_type(),
                    message=f"User {user_id} is already banned",
                    metadata={"user_id": user_id, "already_banned": True}
                )
            
            now = int(time.time() * 1000)
            from src.utils.encryption import generate_snowflake_id
            
            if action.delete_message_history_hours:
                cutoff = now - (action.delete_message_history_hours * 3600 * 1000)
                
                channels = db.fetch_all(
                    "SELECT conversation_id FROM srv_channels WHERE server_id = ?",
                    (server_id,)
                )
                
                for channel in channels:
                    db.execute(
                        """UPDATE msg_messages SET deleted = 1, deleted_at = ?
                           WHERE conversation_id = ? AND author_id = ? AND created_at > ?""",
                        (now, channel["conversation_id"], user_id, cutoff)
                    )
            
            db.execute(
                "DELETE FROM srv_member_roles WHERE server_id = ? AND user_id = ?",
                (server_id, user_id)
            )
            
            db.execute(
                "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
                (server_id, user_id)
            )
            
            ban_id = generate_snowflake_id()
            db.execute(
                """INSERT INTO srv_bans (id, server_id, user_id, reason, banned_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ban_id, server_id, user_id, f"AutoMod: {action.reason or 'Rule violation'}", 0, now)
            )
            
            audit_id = generate_snowflake_id()
            db.execute(
                """INSERT INTO srv_audit_log 
                   (id, server_id, action_type, user_id, target_id, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (audit_id, server_id, "MEMBER_BAN", 0, user_id,
                 f"AutoMod: {action.reason or 'Rule violation'}", now)
            )
            
            logger.debug(f"AutoMod banned user {user_id} from server {server_id}")
            
            if action.notify_user:
                self._notify_user(
                    user_id,
                    server_id,
                    f"You have been banned from the server: {action.reason or 'Rule violation'}",
                    context
                )
            
            return ActionResult(
                success=True,
                action_type=self.get_action_type(),
                message=f"Banned user {user_id} from server {server_id}",
                metadata={
                    "user_id": user_id,
                    "server_id": server_id,
                    "delete_message_hours": action.delete_message_history_hours,
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to ban user {user_id}: {e}")
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error=str(e)
            )
    
    @classmethod
    def get_action_type(cls) -> str:
        return ActionType.BAN_USER.value
