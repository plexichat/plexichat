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

    def _require_alias_send_access(
        self, addressed_id: int, user_id: int, servers_mod: Any
    ) -> None:
        """Fail closed when ``addressed_id`` is a server channel's linked
        conversation id used as a URL alias and the caller cannot post there.

        See the alias-gate comment in ``_send_message_with_fallback``. The
        reverse lookup is membership-agnostic; the backing channel is gated
        with a live ``get_channel`` (view) and ``messages.send`` decision. Any
        lookup/permission that cannot be resolved denies the send.
        """

        def _deny() -> HTTPException:
            return HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        if not servers_mod:
            # The backing-channel lookup cannot be evaluated without the
            # servers module. A private-channel alias can exist in the DB
            # (srv_channels.conversation_id) independently of that module, so
            # skipping this gate when it is unwired would let any participant
            # post into a channel whose messages.send grant was never checked.
            # Deny instead. A *wired* store that is merely uninitialized has no
            # channel table from which an alias could be produced and is
            # handled below, so fail-closed is not a blanket DM denial.
            raise _deny()
        if getattr(servers_mod, "_setup_complete", True) is False:
            # Present but uninitialized: no server-channel store exists here.
            return

        lookup = getattr(servers_mod, "get_channel_by_conversation", None)
        if not callable(lookup):
            raise _deny()
        try:
            info = lookup(int(addressed_id))
        except Exception:
            raise _deny()
        if info is None:
            return  # DM/group conversation: participation gates it in messaging
        channel_id = info.get("id") if isinstance(info, dict) else None
        server_id = info.get("server_id") if isinstance(info, dict) else None
        if channel_id is None or server_id is None:
            raise _deny()

        get_channel = getattr(servers_mod, "get_channel", None)
        timed_out = getattr(servers_mod, "is_timed_out", None)
        has_permission = getattr(servers_mod, "has_permission", None)
        if not (
            callable(get_channel) and callable(timed_out) and callable(has_permission)
        ):
            raise _deny()
        try:
            if get_channel(int(channel_id), user_id) is None:
                raise _deny()
            if timed_out(user_id, int(server_id)):
                raise _deny()
            allowed = bool(
                has_permission(
                    user_id, int(server_id), "messages.send", int(channel_id)
                )
            )
        except HTTPException:
            raise
        except Exception:
            raise _deny()
        if not allowed:
            raise _deny()

        # The alias path never runs the REST route's ``if server_id:`` block,
        # so its only slowmode check would have been there. Apply the same
        # gate to the real backing channel instead. Fail closed: view/send
        # grants alone must not let a member ignore a slow channel simply by
        # addressing it through its linked conversation id.
        self._enforce_channel_slowmode(
            int(channel_id), user_id, servers_mod, fail_closed=True
        )

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
        _channel_ctx: Optional[dict] = None,
    ) -> tuple:
        """
        Send a message with fallback logic: try server channel first, then DM conversation.

        Returns:
            Tuple of (message, server_id)

        ``_channel_ctx`` optionally carries the channel resolved earlier in
        the request (by ``_resolve_server_id``). Reusing it avoids a second
        ``get_channel`` round-trip on every send; callers that do not pass a
        context keep the original lookup behaviour.
        """
        msg = None
        server_id = None
        server_channel_resolved = False

        if servers_mod:
            try:
                if _channel_ctx is not None and "channel" in _channel_ctx:
                    channel = _channel_ctx["channel"]
                else:
                    channel = servers_mod.get_channel(channel_id, user_id)
                if channel:
                    server_channel_resolved = True
                    server_id = getattr(channel, "server_id", None)
                    conversation_id = getattr(channel, "conversation_id", None)

                    if server_id:
                        # The REST send route publishes straight through the
                        # messaging layer instead of the servers manager's
                        # ``send_channel_message`` gate, which is where
                        # ``messages.send`` is normally enforced. Conversation
                        # participation is seeded for every server member on
                        # join and is NOT an authorization check for a server
                        # channel, so a member who can view a read-only channel
                        # could otherwise still post to it through this route.
                        # Fail closed: no permission API, or a failed lookup,
                        # denies the send rather than silently granting it.
                        has_permission = getattr(servers_mod, "has_permission", None)
                        if has_permission is None:
                            logger.warning(
                                "Send denied for channel %s: servers module has "
                                "no permission API",
                                channel_id,
                            )
                            raise HTTPException(
                                status_code=403,
                                detail={
                                    "error": {"code": 403, "message": "Access denied"}
                                },
                            )
                        try:
                            allowed = bool(
                                has_permission(
                                    user_id,
                                    server_id,
                                    "messages.send",
                                    channel_id,
                                )
                            )
                        except Exception as e:
                            logger.warning(
                                f"Send permission check failed for channel "
                                f"{channel_id} (failing closed): {e}"
                            )
                            allowed = False
                        if not allowed:
                            raise HTTPException(
                                status_code=403,
                                detail={
                                    "error": {"code": 403, "message": "Access denied"}
                                },
                            )

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
                        # Fast path: the channel already carries a conversation
                        # link (the common case). Skip the repair round-trip --
                        # ensure_channel_conversation() re-fetches the channel
                        # row and re-checks conversation_exists() on every send.
                        # If the link is stale the send yields no message, and
                        # we repair + retry below.
                        msg = await run_in_threadpool(
                            messaging.send_message,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            content=content,
                            reply_to_id=reply_to_id,
                            attachments=attachments,
                            embeds=embeds,
                        )
                        if msg is None:
                            ensure = getattr(
                                servers_mod, "ensure_channel_conversation", None
                            )
                            if ensure is not None:
                                try:
                                    repaired = await run_in_threadpool(
                                        ensure, channel_id
                                    )
                                except Exception as repair_error:
                                    logger.warning(
                                        f"Could not repair conversation link for "
                                        f"channel {channel_id}: {repair_error}"
                                    )
                                    repaired = None
                                if repaired and str(repaired) != str(conversation_id):
                                    msg = await run_in_threadpool(
                                        messaging.send_message,
                                        user_id=user_id,
                                        conversation_id=repaired,
                                        content=content,
                                        reply_to_id=reply_to_id,
                                        attachments=attachments,
                                        embeds=embeds,
                                    )
                    elif messaging:
                        ensure = getattr(
                            servers_mod, "ensure_channel_conversation", None
                        )
                        if ensure is not None:
                            try:
                                repaired = await run_in_threadpool(ensure, channel_id)
                                if repaired:
                                    conversation_id = repaired
                            except Exception as repair_error:
                                logger.warning(
                                    f"Could not repair conversation link for channel "
                                    f"{channel_id}: {repair_error}"
                                )
                        conversation_id = conversation_id or channel_id
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
            # Message routes also accept a server channel's linked
            # ``conversation_id`` in the ``channel_id`` slot. When that alias is
            # used, ``get_channel(alias, user)`` returns None (an alias is not a
            # ``srv_channels`` row id), so the per-channel ``messages.send`` and
            # timeout checks above never ran and this DM-style fallback is the
            # only remaining gate. Participation is seeded for every server
            # member on join and is NOT a send grant, so posting through the
            # alias would bypass ``messages.send``. Reverse-resolve the alias
            # membership-agnostically and require a live view + send grant on
            # the real backing channel before falling through. A conversation
            # with no backing channel is a genuine DM/group, gated by the
            # messaging layer's participation check.
            if not server_channel_resolved:
                self._require_alias_send_access(channel_id, user_id, servers_mod)
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
        self,
        channel_id: int,
        user_id: int,
        servers_mod: Any,
        _channel_ctx: Optional[dict] = None,
    ) -> Optional[int]:
        """Return the server id for a channel, or None for DM conversations.

        When ``_channel_ctx`` is provided the resolved channel is stored under
        ``"channel"`` so later helpers can reuse it instead of re-fetching the
        same row. A failed lookup is deliberately NOT cached, preserving the
        existing fallback where a later helper re-attempts the lookup and can
        surface an access-denied error as a 403.
        """
        if not servers_mod:
            return None
        try:
            if _channel_ctx is not None and "channel" in _channel_ctx:
                channel = _channel_ctx["channel"]
            else:
                channel = servers_mod.get_channel(channel_id, user_id)
                if _channel_ctx is not None:
                    _channel_ctx["channel"] = channel
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
        _channel_ctx: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Populate automod fast-path context for the live message pipeline.

        Supplies ``recent_messages`` (bounded, for MessageSpamRule) and
        ``known_invite_codes`` (resolved from content, for InviteLinkRule).
        All lookups are best-effort: failures leave the context empty rather
        than delaying or failing the send.
        """
        context: Dict[str, Any] = {"source": source}

        recent = self._recent_messages_for_automod(
            channel_id, user_id, servers_mod, messaging, _channel_ctx
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
        _channel_ctx: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch the last N messages of the target conversation (bounded)."""
        if not messaging:
            return []
        getter = getattr(messaging, "get_messages", None)
        if getter is None:
            return []

        conversation_id = self._conversation_for_channel(
            channel_id, servers_mod, _channel_ctx
        )
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

    def _conversation_for_channel(
        self,
        channel_id: int,
        servers_mod: Any,
        _channel_ctx: Optional[dict] = None,
    ) -> int:
        """Map a URL channel id onto its conversation (server channels link to
        one; DM/group conversations ARE their own channel id).

        A channel resolved earlier in the request is reused so no second
        ``get_channel_conversation_id`` lookup is issued.
        """
        if _channel_ctx is not None and "channel" in _channel_ctx:
            channel = _channel_ctx["channel"]
            if channel is not None:
                linked = getattr(channel, "conversation_id", None)
                if linked:
                    return int(linked)
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

        Results are cached briefly so repeated sends stay on the fast path. On
        a cache miss the candidates are resolved in ONE set-based manager
        lookup instead of one ``get_invite`` DB query per code (previously up
        to ``AUTOMOD_MAX_INVITE_CODES`` round trips on every novel-code send).
        """
        getter = getattr(servers_mod, "get_invite", None) if servers_mod else None
        if getter is None or not content:
            return []

        candidates = list(
            dict.fromkeys(
                m.group(1) for m in self.INVITE_CODE_PATTERN.finditer(content)
            )
        )[: self.AUTOMOD_MAX_INVITE_CODES]

        # Serve cached decisions first; collect misses for a single batched read.
        known = set()
        misses: List[str] = []
        for code in candidates:
            exists = cache_get(f"automod_invite:{code.lower()}")
            if exists is None:
                misses.append(code)
            elif exists:
                known.add(code)

        if misses:
            if hasattr(servers_mod, "get_existing_invite_codes"):
                # One chunked, set-based query resolves every miss. Fail closed
                # (nothing known) if the lookup is unavailable so moderation
                # still sees an empty set rather than a partially resolved one.
                try:
                    found = set(servers_mod.get_existing_invite_codes(misses) or ())
                except Exception as e:
                    logger.debug(f"Batched invite resolution failed: {e}")
                    found = set()
                for code in misses:
                    exists = code in found
                    cache_set(f"automod_invite:{code.lower()}", bool(exists), ttl=300)
                    if exists:
                        known.add(code)
            else:
                # Fallback for managers without the bulk helper; still bounded
                # by AUTOMOD_MAX_INVITE_CODES candidates.
                for code in misses:
                    try:
                        exists = getter(code) is not None
                    except Exception as e:
                        logger.debug(f"Invite resolution failed for '{code}': {e}")
                        continue
                    cache_set(f"automod_invite:{code.lower()}", bool(exists), ttl=300)
                    if exists:
                        known.add(code)

        # Preserve the original candidate ordering for callers.
        return [code for code in candidates if code in known]

    def _enforce_channel_slowmode(
        self, channel_id: int, user_id: int, servers_mod: Any, fail_closed: bool = False
    ) -> None:
        """Reject sends that violate the channel's slowmode interval.

        The REST send path publishes straight through the messaging layer,
        so it never passed the servers manager's own slowmode gate.  This
        reuses that gate (same interval, same moderator exemptions) and
        by default fails open on infrastructure errors so a limiter outage
        cannot blackhole messages.

        ``fail_closed`` is for paths that skip the normal per-channel send
        gate entirely (a server channel addressed by its linked
        conversation-id alias). Those paths have no second slowmode check to
        fall back to, so an unavailable/broken gate must deny the send instead
        of silently dropping the limit.
        """
        checker = getattr(servers_mod, "check_channel_slowmode", None)
        if not servers_mod or checker is None:
            # A quiet skip here is how a missing proxy let a member ignore a
            # slow channel in production without a single log line.
            logger.warning(
                "Channel slowmode gate unavailable on the servers module; "
                f"sends to {channel_id} are not limited"
            )
            if fail_closed:
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            return

        try:
            retry_after = checker(user_id, channel_id)
        except Exception as e:
            outcome = "failing closed" if fail_closed else "failing open"
            logger.warning(
                f"Channel slowmode check failed for {channel_id} ({outcome}): {e}"
            )
            if fail_closed:
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
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

    def _enforce_alias_automod(
        self,
        addressed_id: int,
        content: str,
        user_id: int,
        servers_mod: Any,
        messaging: Any,
        automod: Any = None,
        source: str = "message_create",
        _channel_ctx: Optional[dict] = None,
    ) -> None:
        """Run the server automod fast gate for a conversation-id alias send.

        The REST send route's ``if server_id:`` block is the only place that
        runs ``automod.check_message_fast``. A server channel's linked
        ``conversation_id`` used in the URL resolves to no channel row, so that
        block -- and its moderation -- is skipped entirely. Resolve the alias
        back to its real backing channel/server and apply the same check there.

        A conversation with no backing channel is a genuine DM/group and is
        left to participation plus DM spam protection. Anything that cannot be
        resolved cleanly, or a moderation service that cannot be evaluated,
        denies the send: unlike the normal channel-id path there is no second
        automod gate to fall back to on this route.
        """
        if automod is None:
            from src.core import automod as _automod

            automod = _automod

        def _deny() -> HTTPException:
            return HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        if _channel_ctx is not None and _channel_ctx.get("channel") is not None:
            # A channel row was resolved for this id, so it is a channel id,
            # not a conversation-id alias. There is no alias to reverse-map.
            return
        if not servers_mod or getattr(servers_mod, "_setup_complete", True) is False:
            # No server-channel store from which an alias could be produced.
            # Genuine DMs are gated by participation; alias sends are denied
            # by ``_require_alias_send_access`` in the fallback below.
            return

        lookup = getattr(servers_mod, "get_channel_by_conversation", None)
        if not callable(lookup):
            # Cannot distinguish an alias from a DM: fail closed, matching the
            # alias send gate in ``_require_alias_send_access``.
            raise _deny()
        try:
            info = lookup(int(addressed_id))
        except Exception:
            raise _deny()
        if info is None:
            return  # genuine DM/group conversation: no server automod
        channel_id = info.get("id") if isinstance(info, dict) else None
        server_id = info.get("server_id") if isinstance(info, dict) else None
        if channel_id is None or server_id is None:
            raise _deny()
        channel_id = int(channel_id)
        server_id = int(server_id)

        automod_context = self._build_automod_context(
            channel_id, user_id, content, servers_mod, messaging, source
        )
        violations: list = []
        try:
            fast_result = automod.check_message_fast(
                server_id=server_id,
                channel_id=channel_id,
                user_id=user_id,
                content=content,
                message_id=None,
                context=automod_context,
            )
            if not fast_result.passed:
                violations = list(fast_result.violations or [])
                for match in violations:
                    automod.process_violation(
                        server_id=server_id,
                        channel_id=channel_id,
                        user_id=user_id,
                        message_id=None,
                        match=match,
                        actions=fast_result.actions_to_take,
                        context=automod_context,
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Alias-path automod fast check failed for {channel_id}: {e}")
            try:
                automod.record_internal_error(
                    f"fast_check:{source}:conversation_alias",
                    server_id,
                    channel_id,
                    user_id,
                    None,
                    e,
                )
            except Exception:
                pass
            # No second moderation gate runs on this path: deny rather than
            # publish content that was never checked.
            raise _deny()

        if violations:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "MESSAGE_BLOCKED",
                        "message": "Message blocked by auto-moderation",
                        "violations": [
                            getattr(v.rule_type, "value", v.rule_type)
                            for v in violations
                        ],
                    }
                },
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
                except Exception as badge_error:
                    logger.debug(f"author badge lookup failed: {badge_error}")

        return author_username, author_avatar_url, author_badges
