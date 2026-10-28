"""
Base class with shared helpers for message CRUD mixins.

Provides message validation, channel fallback logic, and utility methods
used across multiple mixins.
"""

import re
from typing import Optional, Any, Dict, List

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.core.database import cache_get, cache_set
from src.core.messaging.exceptions import (
    AttachmentLimitError,
    ConversationAccessDeniedError,
    ConversationNotFoundError,
    InvalidContentError,
    ContentTooLongError,
)
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)


class MessagesCRUDBase:
    # Bounded automod context: how many recent messages feed MessageSpamRule
    # and how many invite codes are resolved per message (fast-path budget).
    AUTOMOD_RECENT_MESSAGE_LIMIT = 25
    AUTOMOD_MAX_INVITE_CODES = 10

    INVITE_CODE_PATTERN = re.compile(
        r"(?:discord\.gg/|discord\.com/invite/)([A-Za-z0-9_-]+)", re.IGNORECASE
    )

    def _resolve_channel_scope(self, cid: int, user_id: int, servers_mod: Any) -> set:
        """Resolve a URL channel id to the conversation ids it can map to.

        A channel id is either a conversation id itself (DM/group) or a
        server channel id whose messages live in the linked conversation.
        Returns the set of both candidates for membership validation.
        """
        scope = {int(cid)}
        if servers_mod:
            getter = getattr(servers_mod, "get_channel_conversation_id", None)
            if getter is not None:
                try:
                    linked = getter(int(cid))
                    if linked:
                        scope.add(int(linked))
                except Exception as e:
                    logger.warning(
                        f"Channel scope resolution failed for channel {cid}: "
                        f"{type(e).__name__}: {e}"
                    )
        return scope

    def _ensure_message_in_channel(self, message: Any, scope: set) -> None:
        """404 when the message does not belong to the addressed channel.

        Uses the SAME error a nonexistent message produces so callers cannot
        probe for message existence in foreign conversations.
        """
        try:
            conversation_id = int(getattr(message, "conversation_id"))
        except (TypeError, ValueError):
            conversation_id = None
        if conversation_id is None or conversation_id not in scope:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )

    @staticmethod
    def _map_messaging_error(e: Exception, not_found_message: str) -> HTTPException:
        """Map core messaging/server exceptions onto HTTP errors.

        Client-visible messages are STATIC (never str(e)) so exception
        internals never leak; details are logged by the caller.
        """
        if isinstance(
            e, (ConversationNotFoundError, ChannelNotFoundError, ServerNotFoundError)
        ):
            return HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": not_found_message}},
            )
        if (
            isinstance(
                e,
                (
                    ConversationAccessDeniedError,
                    ChannelAccessDeniedError,
                    PermissionDeniedError,
                ),
            )
            or "Access" in type(e).__name__
        ):
            return HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )
        if isinstance(e, InvalidContentError):
            return HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message content"}},
            )
        if isinstance(e, ContentTooLongError):
            return HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Message content too long"}},
            )
        if "Content" in type(e).__name__ or "Invalid" in type(e).__name__:
            return HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message content"}},
            )
        if "NotFound" in type(e).__name__:
            return HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": not_found_message}},
            )
        raise ValueError(f"Unmapped messaging error: {type(e).__name__}")

    async def _send_message_with_fallback(
        self,
        channel_id: int,
        user_id: int,
        content: str,
        reply_to_id: Optional[int],
        attachments: Optional[list],
        embeds: Optional[list],
        servers_mod: Any,
        messaging: Any,
    ) -> tuple:
        """
        Send a message with fallback logic: try server channel first, then DM conversation.

        Returns:
            Tuple of (message, server_id)
        """
        msg = None
        server_id = None

        if servers_mod:
            try:
                channel = servers_mod.get_channel(channel_id, user_id)
                if channel:
                    server_id = getattr(channel, "server_id", None)
                    conversation_id = getattr(channel, "conversation_id", None)

                    if server_id and hasattr(servers_mod, "is_timed_out"):
                        if servers_mod.is_timed_out(user_id, server_id):
                            raise HTTPException(
                                status_code=403,
                                detail={
                                    "error": {
                                        "code": 403,
                                        "message": "You are currently timed out in this server",
                                    }
                                },
                            )

                    if conversation_id and messaging:
                        msg = await run_in_threadpool(
                            messaging.send_message,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            content=content,
                            reply_to_id=reply_to_id,
                            attachments=attachments,
                            embeds=embeds,
                        )
                    elif messaging:
                        logger.warning(
                            f"Server channel {channel_id} has no conversation_id linked"
                        )
                        try:
                            msg = await run_in_threadpool(
                                messaging.send_message,
                                user_id=user_id,
                                conversation_id=channel_id,
                                content=content,
                                reply_to_id=reply_to_id,
                                attachments=attachments,
                                embeds=embeds,
                            )
                        except Exception:
                            msg = None
            except HTTPException:
                raise
            except Exception as e:
                if isinstance(e, AttachmentLimitError):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {"code": 400, "message": "Too many attachments"}
                        },
                    )
                if not isinstance(e, (ServerNotFoundError, ChannelNotFoundError)):
                    if isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
                        raise HTTPException(
                            status_code=403,
                            detail={"error": {"code": 403, "message": "Access denied"}},
                        )
                    logger.error(
                        f"Error sending message in server channel {channel_id}: {e}",
                        exc_info=True,
                    )
                    raise
                msg = None

        if msg is None and messaging:
            try:
                msg = messaging.send_message(
                    user_id=user_id,
                    conversation_id=channel_id,
                    content=content,
                    reply_to_id=reply_to_id,
                    attachments=attachments,
                    embeds=embeds,
                )
            except Exception as e:
                exc_name = type(e).__name__
                if isinstance(e, AttachmentLimitError):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {"code": 400, "message": "Too many attachments"}
                        },
                    )
                if (
                    isinstance(e, InvalidContentError)
                    or "Content" in exc_name
                    or "Invalid" in exc_name
                ):
                    logger.warning(f"Message content rejected in channel {channel_id}")
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {"code": 400, "message": "Invalid message content"}
                        },
                    )
                if (
                    isinstance(e, (ConversationNotFoundError,))
                    or "NotFound" in exc_name
                ):
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                if (
                    isinstance(
                        e,
                        (ConversationAccessDeniedError,),
                    )
                    or "Access" in exc_name
                ):
                    # Access-denied is a 403 everywhere else; never misreport
                    # it as not-found.
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": "Access denied"}},
                    )
                logger.error(
                    f"Error sending message in channel {channel_id}: {e}", exc_info=True
                )
                raise

        return msg, server_id

    def _resolve_server_id(
        self, channel_id: int, user_id: int, servers_mod: Any
    ) -> Optional[int]:
        """Return the server id for a channel, or None for DM conversations."""
        if not servers_mod:
            return None
        try:
            channel = servers_mod.get_channel(channel_id, user_id)
            if channel:
                return getattr(channel, "server_id", None)
        except Exception:
            return None
        return None

    def _build_automod_context(
        self,
        channel_id: int,
        user_id: int,
        content: str,
        servers_mod: Any,
        messaging: Any,
        source: str,
    ) -> Dict[str, Any]:
        """Populate automod fast-path context for the live message pipeline.

        Supplies ``recent_messages`` (bounded, for MessageSpamRule) and
        ``known_invite_codes`` (resolved from content, for InviteLinkRule).
        All lookups are best-effort: failures leave the context empty rather
        than delaying or failing the send.
        """
        context: Dict[str, Any] = {"source": source}

        recent = self._recent_messages_for_automod(
            channel_id, user_id, servers_mod, messaging
        )
        if recent:
            context["recent_messages"] = recent

        codes = self._resolve_invite_codes(content, servers_mod)
        if codes:
            context["known_invite_codes"] = codes
        return context

    def _recent_messages_for_automod(
        self,
        channel_id: int,
        user_id: int,
        servers_mod: Any,
        messaging: Any,
    ) -> List[Dict[str, Any]]:
        """Fetch the last N messages of the target conversation (bounded)."""
        if not messaging:
            return []
        getter = getattr(messaging, "get_messages", None)
        if getter is None:
            return []

        conversation_id = self._conversation_for_channel(channel_id, servers_mod)
        try:
            messages = getter(user_id, int(conversation_id), limit=25) or []
        except Exception as e:
            logger.debug(f"Automod recent-message lookup failed for {channel_id}: {e}")
            return []

        recent = []
        for m in messages[: self.AUTOMOD_RECENT_MESSAGE_LIMIT]:
            author_id = getattr(m, "author_id", None)
            if author_id is None:
                continue
            recent.append(
                {
                    "user_id": author_id,
                    "created_at": getattr(m, "created_at", 0),
                    "content": getattr(m, "content", "") or "",
                }
            )
        return recent

    def _conversation_for_channel(self, channel_id: int, servers_mod: Any) -> int:
        """Map a URL channel id onto its conversation (server channels link to
        one; DM/group conversations ARE their own channel id)."""
        if servers_mod:
            getter = getattr(servers_mod, "get_channel_conversation_id", None)
            if getter is not None:
                try:
                    linked = getter(int(channel_id))
                    if linked:
                        return int(linked)
                except Exception as e:
                    logger.debug(
                        f"Conversation lookup failed for channel {channel_id}: {e}"
                    )
        return int(channel_id)

    def _resolve_invite_codes(self, content: str, servers_mod: Any) -> List[str]:
        """Resolve invite codes found in content against real invites.

        Results are cached briefly so repeated sends stay on the fast path.
        """
        getter = getattr(servers_mod, "get_invite", None) if servers_mod else None
        if getter is None or not content:
            return []

        candidates = list(
            dict.fromkeys(
                m.group(1) for m in self.INVITE_CODE_PATTERN.finditer(content)
            )
        )[: self.AUTOMOD_MAX_INVITE_CODES]

        known_codes = []
        for code in candidates:
            cache_key = f"automod_invite:{code.lower()}"
            exists = cache_get(cache_key)
            if exists is None:
                try:
                    exists = getter(code) is not None
                except Exception as e:
                    logger.debug(f"Invite resolution failed for '{code}': {e}")
                    continue
                cache_set(cache_key, bool(exists), ttl=300)
            if exists:
                known_codes.append(code)
        return known_codes

    def _enforce_channel_slowmode(
        self, channel_id: int, user_id: int, servers_mod: Any
    ) -> None:
        """Reject sends that violate the channel's slowmode interval.

        The REST send path publishes straight through the messaging layer,
        so it never passed the servers manager's own slowmode gate.  This
        reuses that gate (same interval, same moderator exemptions) and
        fails open on infrastructure errors so a limiter outage cannot
        blackhole messages.
        """
        checker = getattr(servers_mod, "check_channel_slowmode", None)
        if not servers_mod or checker is None:
            # A quiet skip here is how a missing proxy let a member ignore a
            # slow channel in production without a single log line.
            logger.warning(
                "Channel slowmode gate unavailable on the servers module; "
                f"sends to {channel_id} are not limited"
            )
            return

        try:
            retry_after = checker(user_id, channel_id)
        except Exception as e:
            logger.warning(
                f"Channel slowmode check failed for {channel_id} (failing open): {e}"
            )
            return

        if not retry_after:
            return

        retry_after = float(retry_after)
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": (
                        f"Slowmode is enabled. Try again in {retry_after:.1f}s"
                    ),
                    "retry_after_ms": int(retry_after * 1000),
                }
            },
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    def _enforce_dm_spam_protection(
        self, channel_id: int, sender_id: int, content: str, messaging: Any
    ) -> None:
        """Run the DM spam detector when the target channel is a DM.

        Raises a 403 HTTPException when the detector blocks the message;
        any infrastructure failure fails open with a warning.
        """
        if not messaging:
            return
        conversation_getter = getattr(messaging, "get_conversation", None)
        if conversation_getter is None:
            return
        try:
            conversation = conversation_getter(channel_id, sender_id)
        except Exception as e:
            logger.debug(f"DM conversation lookup failed for {channel_id}: {e}")
            return
        if conversation is None:
            return
        conv_type = getattr(conversation, "conversation_type", None)
        if getattr(conv_type, "value", conv_type) != "dm":
            return

        recipient_id = self._dm_recipient(channel_id, sender_id, messaging)
        if recipient_id is None:
            return

        try:
            from src.core import antispam

            detector = antispam.get_dm_spam_detector()
        except Exception as e:
            logger.warning(f"DM spam detector unavailable (failing open): {e}")
            return
        if detector is None:
            return

        allowed, reason = detector.check_message(sender_id, recipient_id, content)
        if not allowed:
            logger.warning(
                f"DM spam blocked message from {sender_id} to {recipient_id}: {reason}"
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Message blocked by spam protection",
                    }
                },
            )

    def _dm_recipient(
        self, conversation_id: int, sender_id: int, messaging: Any
    ) -> Optional[int]:
        """Return the other participant of a DM conversation, if any."""
        getter = getattr(messaging, "get_participant_ids", None)
        if getter is None:
            return None
        try:
            participant_ids = getter(conversation_id) or []
        except Exception as e:
            logger.debug(f"DM participant lookup failed for {conversation_id}: {e}")
            return None
        for pid in participant_ids:
            try:
                pid_int = int(pid)
            except (TypeError, ValueError):
                continue
            if pid_int != int(sender_id):
                return pid_int
        return None

    def _resolve_author_info(self, current_user, auth=None):
        author_username = current_user.username
        author_avatar_url = getattr(current_user, "avatar_url", None)
        author_badges = getattr(current_user, "badges", [])

        if not author_avatar_url or not author_badges:
            resolved_auth = auth or api.get_auth()
            if resolved_auth:
                try:
                    user = resolved_auth.get_user(current_user.user_id)
                    if user:
                        if not author_avatar_url:
                            author_avatar_url = getattr(user, "avatar_url", None)
                        if not author_badges:
                            author_badges = getattr(user, "badges", [])
                except Exception:
                    pass

        return author_username, author_avatar_url, author_badges
