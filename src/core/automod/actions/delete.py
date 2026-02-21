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
            bot_user_id = context.get("bot_user_id") if context else None
            if not bot_user_id:
                # Use hard delete for AutoMod violations
                self._db.execute(
                    "DELETE FROM msg_messages WHERE id = ?",
                    (violation.message_id,),
                )
                # Also delete associated attachments from DB (files stay until cleanup)
                self._db.execute(
                    "DELETE FROM msg_attachments WHERE message_id = ?",
                    (violation.message_id,),
                )

                # Manual invalidation since we used raw SQL
                from src.core.database import cache_delete, invalidate_pattern

                cache_delete(f"msg:obj:{violation.message_id}")
                invalidate_pattern(f"*messages_list:*{violation.channel_id}*")
                invalidate_pattern(f"*messages_api:*{violation.channel_id}*")
                # Clear recent messages Redis list too
                try:
                    from src.core.database import get_redis_client as get_client

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

            # Broadcast deletion to all clients via WebSocket
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

                        # We use a helper to run the async dispatch
                        async def dispatch():
                            await dispatcher.dispatch_event(event, user_ids)

                        try:
                            loop = asyncio.get_running_loop()
                            asyncio.run_coroutine_threadsafe(dispatch(), loop)
                        except RuntimeError:
                            # Fallback if no loop in this thread
                            asyncio.run(dispatch())

            except Exception as we:
                logger.debug(f"Failed to broadcast AutoMod deletion: {we}")

            return True

        except Exception as e:
            logger.error(f"Failed to delete message {violation.message_id}: {e}")
            return False

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
