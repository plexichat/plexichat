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
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Delete the message."""
        if not violation.message_id:
            logger.warning(
                f"Cannot delete message: no message_id in violation {violation.id}"
            )
            return False

        if not self._messaging:
            logger.warning("Cannot delete message: messaging module not available")
            return False

        try:
            # Replacement mode (async AI moderation): the message is not
            # removed from the database. Its content is blanked and an
            # ``automod_removed`` metadata flag is set so the client renders
            # a tombstone. The stored violation id powers the admin-only
            # audit-log link. Rendering is flag-driven, never content based.
            if (context or {}).get("__automod_replacement__"):
                return self._replace_message(violation, context)

            bot_user_id = context.get("bot_user_id") if context else None
            if not bot_user_id:
                # SECURITY: similar to ban.py / kick.py, the previous
                # raw-SQL DELETE path bypassed the messaging
                # permission layer. When ``bot_user_id`` is missing we
                # now refuse the action unless explicit, audited
                # system context is supplied. The ``self._messaging``
                # reference is the permission-aware helper.
                system_context = bool((context or {}).get("__system_context__"))
                if not (self._messaging and system_context):
                    logger.error(
                        "AutoMod message delete REFUSED: bot_user_id "
                        "missing and no permission-checked system "
                        "context was supplied."
                    )
                    return False

                # Use hard delete for AutoMod violations (only via
                # the messaging module so cache invalidation and
                # audit hooks are honoured correctly — the legacy
                # raw-SQL branch is intentionally removed).
                self._messaging.delete_message(
                    user_id=0,  # 0 = system actor in audit trail
                    message_id=violation.message_id,
                    hard_delete=True,
                )

                # Manual invalidation since we used raw SQL
                from src.core.database import cache_delete, invalidate_pattern

                cache_delete(f"msg:obj:{violation.message_id}")
                invalidate_pattern(f"*messages_list:*{violation.channel_id}*")
                invalidate_pattern(f"*messages_api:*{violation.channel_id}*")
                # Clear recent messages Valkey list too
                try:
                    from src.core.database import get_valkey_client as get_client

                    client = get_client()
                    if client:
                        client.delete(f"msg:recent:{violation.channel_id}")
                except Exception:
                    pass
            else:
                self._messaging.delete_message(
                    user_id=bot_user_id,
                    message_id=violation.message_id,
                    hard_delete=True,
                )

            logger.debug(
                f"Deleted message {violation.message_id} for violation {violation.id}"
            )

            self._broadcast_removed(violation)

            return True

        except Exception as e:
            logger.error(f"Failed to delete message {violation.message_id}: {e}")
            return False

    def _replace_message(self, violation: Violation, context) -> bool:
        """Replace the offending message with an AutoMod tombstone."""
        if not self._messaging:
            logger.warning("Cannot replace message: messaging module not available")
            return False

        reason = None
        if context:
            reason = context.get("__automod_reason__") or violation.matched_content
        rule_name = None
        if violation.rule_id:
            try:
                from src.core import automod

                rule = automod.get_rule(violation.rule_id)
                rule_name = rule.name if rule else None
            except Exception:
                rule_name = None

        try:
            msg = self._messaging.automod_remove_message(
                violation.message_id,
                violation_id=violation.id,
                rule_name=rule_name,
                reason=reason,
            )
        except Exception as e:
            logger.error(f"Failed to replace message {violation.message_id}: {e}")
            return False

        if msg is None:
            logger.warning(
                f"Message {violation.message_id} no longer exists; cannot tombstone"
            )
            return False

        logger.debug(
            f"Replaced message {violation.message_id} for violation {violation.id}"
        )
        self._broadcast_updated(violation, msg)
        return True

    def _broadcast_updated(self, violation: Violation, msg) -> None:
        """Broadcast a MESSAGE_UPDATE event so clients re-render the tombstone."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType
            from src.api.routes.messages.helpers import _message_to_response
            import asyncio

            if not ws_is_setup():
                return

            response = _message_to_response(msg, channel_id=violation.channel_id)
            dispatcher = get_dispatcher()
            user_ids = []
            if self._servers:
                try:
                    user_ids = self._servers.get_member_user_ids(violation.server_id)
                except Exception:
                    pass

            if user_ids:
                event = Event(
                    event_type=EventType.MESSAGE_UPDATE,
                    data=response.model_dump(),
                    server_id=violation.server_id,
                    channel_id=violation.channel_id,
                )

                async def dispatch():
                    await dispatcher.dispatch_event(event, user_ids)

                try:
                    loop = asyncio.get_running_loop()
                    asyncio.run_coroutine_threadsafe(dispatch(), loop)
                except RuntimeError:
                    asyncio.run(dispatch())
        except Exception as we:
            logger.debug(f"Failed to broadcast AutoMod tombstone update: {we}")

    def _broadcast_removed(self, violation: Violation) -> None:
        """Broadcast a MESSAGE_DELETE event to all clients."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType
            import asyncio

            if ws_is_setup():
                dispatcher = get_dispatcher()
                user_ids = []
                if self._servers:
                    try:
                        user_ids = self._servers.get_member_user_ids(
                            violation.server_id
                        )
                    except Exception:
                        pass

                if user_ids:
                    event = Event(
                        event_type=EventType.MESSAGE_DELETE,
                        data={
                            "id": str(violation.message_id),
                            "channel_id": str(violation.channel_id),
                            "server_id": str(violation.server_id),
                            "automod": True,
                        },
                        server_id=violation.server_id,
                        channel_id=violation.channel_id,
                    )

                    async def dispatch():
                        await dispatcher.dispatch_event(event, user_ids)

                    try:
                        loop = asyncio.get_running_loop()
                        asyncio.run_coroutine_threadsafe(dispatch(), loop)
                    except RuntimeError:
                        asyncio.run(dispatch())

        except Exception as we:
            logger.debug(f"Failed to broadcast AutoMod deletion: {we}")

    def can_execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Check if message can be deleted."""
        if not violation.message_id:
            return False, "No message ID available"

        return True, None
