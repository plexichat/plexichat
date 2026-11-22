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
                f"""SELECT DISTINCT m.user_id FROM srv_member_roles mr
                    JOIN srv_members m ON m.id = mr.member_id
                    WHERE m.server_id = ? AND mr.role_id IN ({placeholders})""",
                (server_id, *role_ids),
            )
            for m in members:
                mod_ids.add(m["user_id"])

        return list(mod_ids)

    def _get_alert_channel(self, server_id: int) -> Optional[int]:
        """Get the configured alert channel for a server.

        Resolution order:
          1. Server scope: the server's ``system_channel_id``.
          2. Global scope: the ``automod.default_alert_channel_id`` config key.

        The previous implementation scraped an arbitrary automod_rules row for
        an ``alert_channel_id`` key that nothing ever set; that dead lookup
        path has been removed.
        """
        server_row = self._db.fetch_one(
            "SELECT system_channel_id FROM srv_servers WHERE id = ?", (server_id,)
        )
        if server_row and server_row["system_channel_id"]:
            return server_row["system_channel_id"]

        automod_config = config.get("automod", {}) or {}
        default_channel = automod_config.get("default_alert_channel_id")
        if default_channel:
            try:
                return int(default_channel)
            except (TypeError, ValueError):
                return None

        return None

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

        # The full preview contains the offending message body, which is
        # private to users who can view the violating channel. Do NOT attach it
        # unconditionally: ``_create_notification`` bypasses the mention
        # fan-out's channel-view gate (it is the low-level writer automod and
        # other direct callers use), so a moderator denied view would otherwise
        # receive the private body in the websocket NOTIFICATION_CREATE event
        # and in the device push payload built from ``content_preview``.
        # Whether to attach it is decided per recipient by the authoritative
        # view gate below; denied moderators get an empty preview.
        full_preview = self._format_alert_message(violation)

        # Resolve notification preferences for the whole moderator audience in
        # two bounded reads instead of two lookups per moderator. The old loop
        # called ``get_notification_settings`` and ``get_channel_override`` once
        # per recipient, so an alert in a server with hundreds of moderators
        # issued 2N config queries (each settings read is a cache get + a
        # ``SELECT``). Both helpers below chunk their ``IN (...)`` binds, so the
        # fan-out stays within SQLite/Postgres variable limits; results are
        # keyed by user id and lookups below stay scoped to that one moderator.
        # Missing rows fall back to the same defaults the per-user helpers
        # returned, so eligibility (and fail-closed behaviour) is unchanged.
        # SECURITY: only a genuine integer id from the authoritative moderator
        # roster (srv_servers.owner_id / srv_members.user_id) may attend the
        # ALERT_MODERATORS audience. moderator_ids is the raw DB projection with
        # no type filtering, so int() laundering of a malformed-but-coercible
        # value ("07"->7, " 8 "->8, 7.9->7, True->1) would alert the wrong
        # principal. The downstream _should_notify_user_optimized gate only
        # checks preferences/focus, never moderator authority, so this is the
        # sole audience gate. type() also rejects bool; genuine ints preserved.
        audience = list(
            dict.fromkeys(mid for mid in moderator_ids if type(mid) is int)
        )
        all_settings = manager.get_notification_settings_bulk(
            audience, violation.server_id
        )
        all_overrides = (
            manager.get_channel_overrides_bulk(audience, channel_id)
            if channel_id
            else {}
        )

        for moderator_id in audience:
            settings = all_settings.get(moderator_id)
            override = all_overrides.get(moderator_id)
            # The shared eligibility gate is ``_should_notify_user_optimized``
            # (there is no ``_should_notify_user``); filtering per moderator
            # here keeps each recipient's own preferences authoritative and
            # never mixes one moderator's settings into another's decision.
            if not manager._should_notify_user_optimized(
                moderator_id,
                violation.user_id,
                violation.server_id,
                channel_id,
                MentionType.USER,
                settings,
                override,
            ):
                continue

            # SECURITY: the notification writer below does not re-check channel
            # view. Gate here, *before* attaching the preview, so a moderator
            # who cannot view the violating channel receives no private body.
            # Fail closed: an unwired store, a missing channel, or a raising
            # permission check all strip the content.
            content_preview = (
                full_preview
                if self._moderator_can_view_channel(
                    manager, moderator_id, violation.server_id, channel_id
                )
                else ""
            )

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

    def _moderator_can_view_channel(
        self, manager, moderator_id, server_id, channel_id
    ) -> bool:
        """Authoritative view gate for attaching private content to an alert.

        A moderator may be in the alert audience (owner/mod role) yet lack
        view access to the specific private channel the violation occurred in.
        The notification writer ``_create_notification`` does not re-check
        view, so this is the sole authority for whether the offending message
        body may be attached. Require BOTH the channel-view bit (the same gate
        the mention fan-out uses) and the message-read bit (view access to the
        message body). Fail closed on an unwired store, a missing channel, or
        any error from the permission lookup.
        """
        if not channel_id:
            return False

        servers = self._servers
        if servers is None:
            servers = getattr(manager, "_servers", None)
        get_manager = getattr(servers, "_get_manager", None)
        if callable(get_manager):
            try:
                servers = get_manager()
            except Exception:
                return False

        has_permission = getattr(servers, "has_permission", None)
        if not callable(has_permission):
            return False
        for permission in ("channels.view", "messages.read"):
            try:
                if not has_permission(moderator_id, server_id, permission, channel_id):
                    return False
            except Exception:
                return False
        return True

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
