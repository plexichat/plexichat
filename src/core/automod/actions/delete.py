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

            self._broadcast_removed(violation, context)

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
        self._broadcast_updated(violation, msg, context)
        return True

    def _resolve_recipients(
        self, violation: Violation, context: Optional[Dict[str, Any]]
    ) -> tuple:
        """Resolve broadcast recipients through the shared route-level filter.

        Delegates to the messages CRUD ``_resolve_recipients`` so AutoMod
        broadcasts obey the same rules as user-triggered ones: server
        channels fan out ONLY to members holding ``channels.view`` on the
        channel (the actor is not a member, hence the server_id hint), and
        DM/group conversations (``server_id is None``) fan out to their
        participants instead of vanishing.
        """
        from src.api.routes.messages.messages_crud.broadcast import (
            _resolve_recipients as resolve,
        )

        actor_id = (context or {}).get("bot_user_id") or 0
        return resolve(
            self._servers,
            self._messaging,
            violation.channel_id,
            None,
            actor_id,
            server_id_hint=violation.server_id,
        )

    def _broadcast_updated(
        self, violation: Violation, msg, context: Optional[Dict[str, Any]] = None
    ) -> None:
        """Broadcast a MESSAGE_UPDATE event so clients re-render the tombstone.

        The client merges MESSAGE_UPDATE payloads as a spread-PATCH, so any
        default field in the dump OVERWRITES live client state. The response
        therefore carries real author identity (username/avatar/badges via
        the auth module), the message's actual reactions, and a poll
        hydrated for the author as context viewer — instead of the
        state-clobbering defaults ("User {id}", [], null).
        """
        try:
            import asyncio

            import src.api as api
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events import build_message_update
            from src.api.routes.messages.helpers import _message_to_response

            if not ws_is_setup():
                return

            author_id = getattr(msg, "author_id", None)

            author_username = None
            author_avatar_url = None
            author_badges = []
            auth = api.get_auth()
            if auth and author_id is not None:
                try:
                    user = auth.get_user(int(author_id))
                    if user:
                        author_username = user.username
                        author_avatar_url = getattr(user, "avatar_url", None)
                        author_badges = getattr(user, "badges", []) or []
                except Exception as e:
                    logger.debug(f"AutoMod tombstone author lookup failed: {e}")

            reactions_data = []
            reactions_module = api.get_reactions()
            if reactions_module:
                try:
                    # Viewer id 0: no per-viewer "me" flags or block
                    # filtering in a fan-out broadcast; counts stay real.
                    reactions_data = reactions_module.get_reactions_batch(
                        0, [violation.message_id]
                    ).get(violation.message_id, [])
                except Exception as e:
                    logger.debug(f"AutoMod tombstone reactions lookup failed: {e}")

            response = _message_to_response(
                msg,
                author_username=author_username,
                author_avatar_url=author_avatar_url,
                author_badges=author_badges,
                channel_id=violation.channel_id,
                reactions_data=reactions_data,
                # Hydrate the poll (metadata.poll_id survives removal) using
                # the AUTHOR as context viewer so the poll UI survives the
                # merge; results visibility is enforced against that viewer.
                poll_viewer_user_id=int(author_id) if author_id else None,
            )
            dispatcher = get_dispatcher()
            user_ids, resolved_server_id = self._resolve_recipients(violation, context)

            if user_ids:
                event = build_message_update(
                    response.model_dump(),
                    channel_id=violation.channel_id,
                    server_id=resolved_server_id or violation.server_id,
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

    def _broadcast_removed(
        self, violation: Violation, context: Optional[Dict[str, Any]] = None
    ) -> None:
        """Broadcast a MESSAGE_DELETE event to clients allowed to see it."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events import build_message_delete
            import asyncio

            if ws_is_setup():
                dispatcher = get_dispatcher()
                user_ids, resolved_server_id = self._resolve_recipients(
                    violation, context
                )

                if user_ids:
                    event = build_message_delete(
                        violation.message_id,
                        violation.channel_id,
                        server_id=resolved_server_id or violation.server_id,
                        extra={"automod": True},
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
