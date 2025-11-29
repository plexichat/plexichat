"""
Alert moderators action - Sends alerts to moderators about violations.
"""

import time
import json
from typing import Dict, Any

import utils.logger as logger

from .base import BaseAction, ActionResult
from ..models import RuleAction, Violation, ActionType


class AlertModeratorsAction(BaseAction):
    """Action that alerts moderators about a violation."""
    
    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Dict[str, Any]
    ) -> ActionResult:
        """Execute moderator alert."""
        server_id = violation.server_id
        
        db = context.get("db")
        if not db:
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error="Database not available"
            )
        
        try:
            config = db.fetch_one(
                "SELECT alert_channel_id, alert_webhook_url FROM automod_config WHERE server_id = ?",
                (server_id,)
            )
            
            alert_sent = False
            alert_methods = []
            
            if config and config["alert_channel_id"]:
                channel_sent = self._send_channel_alert(db, config["alert_channel_id"], violation, context)
                if channel_sent:
                    alert_sent = True
                    alert_methods.append("channel")
            
            if config and config["alert_webhook_url"]:
                webhook_sent = self._send_webhook_alert(config["alert_webhook_url"], violation, context)
                if webhook_sent:
                    alert_sent = True
                    alert_methods.append("webhook")
            
            if not alert_sent:
                logger.warning(f"No alert destination configured for server {server_id}")
                return ActionResult(
                    success=False,
                    action_type=self.get_action_type(),
                    error="No alert destination configured"
                )
            
            logger.debug(f"AutoMod sent alert for violation {violation.id} via {alert_methods}")
            
            return ActionResult(
                success=True,
                action_type=self.get_action_type(),
                message=f"Alert sent via {', '.join(alert_methods)}",
                metadata={
                    "violation_id": violation.id,
                    "alert_methods": alert_methods,
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to send alert for violation {violation.id}: {e}")
            return ActionResult(
                success=False,
                action_type=self.get_action_type(),
                error=str(e)
            )
    
    def _send_channel_alert(self, db, channel_id: int, violation: Violation, context: Dict[str, Any]) -> bool:
        """Send alert to a channel."""
        try:
            channel = db.fetch_one(
                "SELECT conversation_id FROM srv_channels WHERE id = ?",
                (channel_id,)
            )
            
            if not channel:
                return False
            
            now = int(time.time() * 1000)
            from src.utils.encryption import generate_snowflake_id
            
            alert_content = self._format_alert_message(violation)
            
            msg_id = generate_snowflake_id()
            db.execute(
                """INSERT INTO msg_messages 
                   (id, conversation_id, author_id, content, message_type, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (msg_id, channel["conversation_id"], 0, alert_content, "system", now, now)
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send channel alert: {e}")
            return False
    
    def _send_webhook_alert(self, webhook_url: str, violation: Violation, context: Dict[str, Any]) -> bool:
        """Send alert via webhook."""
        try:
            import urllib.request
            
            payload = {
                "content": None,
                "embeds": [{
                    "title": "AutoMod Alert",
                    "description": self._format_alert_message(violation),
                    "color": 0xFF0000,
                    "fields": [
                        {"name": "User ID", "value": str(violation.user_id), "inline": True},
                        {"name": "Channel ID", "value": str(violation.channel_id), "inline": True},
                        {"name": "Rule Type", "value": violation.rule_type.value, "inline": True},
                        {"name": "Severity", "value": violation.severity.value, "inline": True},
                    ],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }]
            }
            
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status in (200, 204)
                
        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")
            return False
    
    def _format_alert_message(self, violation: Violation) -> str:
        """Format alert message content."""
        parts = [
            f"**AutoMod Violation Detected**",
            f"User: <@{violation.user_id}>",
            f"Channel: <#{violation.channel_id}>",
            f"Rule: {violation.rule_type.value}",
            f"Severity: {violation.severity.value}",
        ]
        
        if violation.matched_content:
            parts.append(f"Matched: {violation.matched_content[:100]}")
        
        return "\n".join(parts)
    
    @classmethod
    def get_action_type(cls) -> str:
        return ActionType.ALERT_MODERATORS.value
