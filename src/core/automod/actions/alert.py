"""
Alert moderators action.

Sends notifications to server moderators about violations.
"""

import time
import json
from typing import Dict, Any, Optional, List

import utils.logger as logger
import utils.config as config
from src.utils.encryption import generate_snowflake_id

from .base import BaseAction
from ..models import ActionType, RuleAction, Violation
from src.core.notifications.models import MentionType


class AlertModeratorsAction(BaseAction):
    """Action that alerts moderators about a violation."""

    action_type = ActionType.ALERT_MODERATORS

    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send alert to moderators."""
        try:
            moderator_ids = self._get_moderator_ids(violation.server_id)

            if not moderator_ids:
                logger.debug(f"No moderators to alert for server {violation.server_id}")
                return True

            alert_channel_id = self._get_alert_channel(violation.server_id)

            if alert_channel_id and self._messaging:
                self._send_channel_alert(alert_channel_id, violation, context)

            if self._notifications:
                self._send_notifications(moderator_ids, violation, context)

            self._log_alert(violation, moderator_ids)

            logger.debug(
                f"Alerted {len(moderator_ids)} moderators about violation {violation.id}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to alert moderators: {e}")
            return False

    def _get_moderator_ids(self, server_id: int) -> List[int]:
        """Get IDs of users with moderation permissions."""
        server = self._db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ?", (server_id,)
        )

        mod_ids = set()
        if server:
            mod_ids.add(server["owner_id"])

        mod_roles = self._db.fetch_all(
            """SELECT id FROM srv_roles 
               WHERE server_id = ? AND (
                   permissions LIKE '%"administrator": true%' OR
                   permissions LIKE '%"members.kick": true%' OR
                   permissions LIKE '%"members.ban": true%'
               )""",
            (server_id,),
        )

        role_ids = [r["id"] for r in mod_roles]

        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            members = self._db.fetch_all(
                f"""SELECT DISTINCT user_id FROM srv_member_roles
                    WHERE server_id = ? AND role_id IN ({placeholders})""",
                (server_id, *role_ids),
            )
            for m in members:
                mod_ids.add(m["user_id"])

        return list(mod_ids)

    def _get_alert_channel(self, server_id: int) -> Optional[int]:
        """Get the configured alert channel for a server."""
        automod_config = config.get("automod", {})

        server_config = self._db.fetch_one(
            "SELECT config FROM automod_rules WHERE server_id = ? LIMIT 1", (server_id,)
        )

        if server_config:
            try:
                cfg = json.loads(server_config["config"])
                if "alert_channel_id" in cfg:
                    return cfg["alert_channel_id"]
            except (json.JSONDecodeError, KeyError):
                pass

        return automod_config.get("default_alert_channel_id")

    def _send_channel_alert(
        self, channel_id: int, violation: Violation, context: Optional[Dict[str, Any]]
    ):
        """Send alert message to channel."""
        content = self._format_alert_message(violation)

        try:
            bot_user_id = context.get("bot_user_id") if context else None
            if bot_user_id and self._messaging:
                conv = self._db.fetch_one(
                    "SELECT conversation_id FROM srv_channels WHERE id = ?",
                    (channel_id,),
                )
                if conv:
                    self._messaging.send_message(
                        user_id=bot_user_id,
                        conversation_id=conv["conversation_id"],
                        content=content,
                    )
        except Exception as e:
            logger.warning(f"Failed to send channel alert: {e}")

    def _send_notifications(
        self,
        moderator_ids: List[int],
        violation: Violation,
        context: Optional[Dict[str, Any]],
    ):
        """Send notifications to moderators."""
        if not self._notifications:
            return

        try:
            manager = self._notifications._get_manager()
        except Exception:
            return

        now = int(time.time() * 1000)
        message_id = violation.message_id or 0
        conversation_id = 0
        channel_id = violation.channel_id

        if violation.message_id:
            msg = self._db.fetch_one(
                "SELECT id, conversation_id, channel_id FROM msg_messages WHERE id = ?",
                (violation.message_id,),
            )
            if msg:
                message_id = msg["id"]
                conversation_id = msg.get("conversation_id") or 0
                channel_id = msg.get("channel_id") or channel_id

        if conversation_id == 0 and channel_id:
            conv = self._db.fetch_one(
                "SELECT conversation_id FROM srv_channels WHERE id = ?", (channel_id,)
            )
            if conv:
                conversation_id = conv["conversation_id"]

        content_preview = self._format_alert_message(violation)

        for moderator_id in moderator_ids:
            if not manager._should_notify_user(
                moderator_id,
                violation.user_id,
                violation.server_id,
                channel_id,
                MentionType.USER,
            ):
                continue

            notif = manager._create_notification(
                user_id=moderator_id,
                author_id=violation.user_id,
                message_id=message_id,
                conversation_id=conversation_id,
                server_id=violation.server_id,
                channel_id=channel_id,
                thread_id=None,
                mention_type=MentionType.USER,
                content_preview=content_preview,
                created_at=now,
            )
            if notif:
                manager._update_unread_count(
                    moderator_id,
                    conversation_id,
                    violation.server_id,
                    channel_id,
                    is_mention=True,
                )

    def _format_alert_message(self, violation: Violation) -> str:
        """Format the alert message."""
        return (
            f"[AutoMod] Violation detected\n"
            f"User: <@{violation.user_id}>\n"
            f"Rule: {violation.rule_type.value}\n"
            f"Severity: {violation.severity.value}\n"
            f"Content: {violation.matched_content[:100]}"
        )

    def _log_alert(self, violation: Violation, moderator_ids: List[int]):
        """Log the alert in the database."""
        now = int(time.time() * 1000)
        alert_id = generate_snowflake_id()

        self._db.execute(
            """INSERT INTO automod_audit 
               (id, server_id, action_type, target_user_id, moderator_id, rule_id, reason, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                alert_id,
                violation.server_id,
                ActionType.ALERT_MODERATORS.value,
                violation.user_id,
                None,
                violation.rule_id,
                f"Alert sent to {len(moderator_ids)} moderators",
                json.dumps({"moderator_ids": moderator_ids}),
                now,
            ),
        )



