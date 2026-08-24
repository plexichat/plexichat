"""
Presence handlers - Handle presence-related opcodes (presence_update, typing).
"""

from typing import Optional, Dict, Any, Tuple, List, TYPE_CHECKING

import utils.logger as logger
from starlette.concurrency import run_in_threadpool

from src.api.websocket.connection import Connection
from src.api.websocket.opcodes import GatewayCloseCode

if TYPE_CHECKING:
    from src.core.presence.manager import PresenceManager


def _get_channel_recipients(
    user_id: int, channel_id: int
) -> Tuple[List[int], Optional[int]]:
    """Resolve the users who should receive typing events for a channel.

    Server channels: the members who can still view the channel. DM
    conversations: the other participants. Returns ``([], None)`` when the
    user has no access to the channel (or it does not exist), which callers
    use both to build the correct audience and to validate the channel.

    Returns:
        Tuple of (recipient_user_ids, server_id_or_None)
    """
    import src.api as api

    user_ids: List[int] = []
    server_id: Optional[int] = None

    servers_mod = api.get_servers()
    if servers_mod:
        try:
            # CORRECTNESS FIX: resolve the channel ONCE for the sender, then
            # filter members with cached permission checks. Previously every
            # member triggered a full get_channel() (channel row lookup +
            # membership query + permission check) per typing event.
            channel = servers_mod.get_channel(channel_id, user_id)
            if channel:
                sid = getattr(channel, "server_id", None)
                if sid:
                    server_id = sid
                    member_user_ids = servers_mod.get_member_user_ids(
                        sid, exclude_user_id=user_id
                    )
                    has_perm = getattr(servers_mod, "has_permission", None)
                    for member_user_id in member_user_ids:
                        try:
                            if callable(has_perm):
                                if has_perm(
                                    member_user_id, sid, "channels.view", channel_id
                                ):
                                    user_ids.append(member_user_id)
                            elif servers_mod.get_channel(channel_id, member_user_id):
                                user_ids.append(member_user_id)
                        except Exception:
                            continue
        except Exception:
            pass

    if not user_ids:
        messaging = api.get_messaging()
        if messaging:
            try:
                participants = messaging.get_participants(user_id, channel_id)
                if participants:
                    user_ids = [p.user_id for p in participants if p.user_id != user_id]
            except Exception:
                pass

    return user_ids, server_id


class PresenceHandler:
    """Handles presence-related opcodes."""

    def __init__(self, presence_module: Optional["PresenceManager"] = None):
        """
        Initialize the presence handler.

        Args:
            presence_module: Presence module for status updates
        """
        self._presence: Optional["PresenceManager"] = presence_module

    async def handle_presence_update(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle presence update opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data or not self._presence:
            return None, None, None

        status = data.get("status", "online")
        custom_status = data.get("custom_status")
        custom_emoji = data.get("custom_emoji")
        activities = data.get("activities", [])

        try:
            from src.core.presence import UserStatus

            status_enum = UserStatus(status)
        except ValueError:
            logger.warning(
                f"User {connection.user_id} sent invalid presence status: {status}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        activity = None
        if activities:
            try:
                from src.core.presence import ActivityType

                activity_type = ActivityType(activities[0].get("type", "custom"))
                activity = {
                    "activity_type": activity_type,
                    "name": activities[0].get("name", ""),
                    "details": activities[0].get("details"),
                    "state": activities[0].get("state"),
                }
            except (ValueError, KeyError, TypeError):
                logger.warning(
                    f"User {connection.user_id} sent invalid activity data: {activities[0]}"
                )
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            assert connection.user_id is not None
            uid = connection.user_id
            await run_in_threadpool(self._presence.set_status, uid, status_enum)

            if activity:
                activity_name = activity.get("name", "").strip()
                if activity_name:
                    try:
                        await run_in_threadpool(
                            self._presence.set_activity,
                            uid,
                            activity["activity_type"],
                            activity_name,
                            details=activity["details"],
                            state=activity["state"],
                        )
                    except Exception:
                        logger.warning(f"Failed to set activity for user {uid}")

            # Persist + broadcast custom status. (P2 fix)
            #
            # ``custom_status`` is only present when the client is explicitly
            # updating it. When it is omitted (e.g. a reconnect that just
            # re-asserts the connection status) we must NOT wipe the existing
            # persisted custom status, so we leave it untouched and simply
            # reflect it in the broadcast.
            broadcast_status = status if status != "invisible" else "offline"
            broadcast_custom: Optional[str] = None
            broadcast_emoji: Optional[str] = None
            if custom_status is not None or custom_emoji is not None:
                text = (custom_status or "").strip()
                emoji = (custom_emoji or "").strip() if custom_emoji else None
                # CORRECTNESS FIX: an emoji-only status (empty text, non-empty
                # emoji) is a valid persisted status — the core manager
                # explicitly supports it. Only empty-text AND empty-emoji is
                # a clear.
                if text or emoji:
                    await run_in_threadpool(
                        self._presence.set_custom_status, uid, text, emoji
                    )
                    broadcast_custom = text if broadcast_status != "offline" else None
                    broadcast_emoji = emoji if broadcast_status != "offline" else None
                else:
                    await run_in_threadpool(self._presence.clear_custom_status, uid)
            else:
                try:
                    pres = await run_in_threadpool(self._presence.get_presence, uid)
                    if pres and pres.custom_status:
                        cs_text = pres.custom_status.text
                        cs_emoji = pres.custom_status.emoji
                        broadcast_custom = (
                            cs_text if broadcast_status != "offline" else None
                        )
                        broadcast_emoji = (
                            cs_emoji if broadcast_status != "offline" else None
                        )
                except Exception:
                    logger.debug(f"Failed to read persisted presence for {uid}")

            await self._dispatch_presence_to_friends(
                uid,
                broadcast_status,
                broadcast_custom,
                broadcast_emoji,
            )
        except Exception as e:
            logger.warning(f"Presence update failed: {e}")

        return None, None, None

    async def handle_typing_start(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle typing start opcode - user started typing in a channel."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, None

        channel_id = data.get("channel_id")
        if not channel_id:
            return None, None, None

        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            return None, None, None

        assert connection.user_id is not None

        # Resolve the channel and its viewers first. This doubles as channel
        # validation: typing must never be recorded for a channel the user
        # cannot access (CORRECTNESS FIX).
        recipients, server_id = await run_in_threadpool(
            _get_channel_recipients, connection.user_id, channel_id
        )
        if not recipients:
            return None, None, None

        # Get username for the typing event
        username = None
        import src.api as api

        auth = api.get_auth()
        if auth:
            try:
                user = await run_in_threadpool(auth.get_user, connection.user_id)
                if user:
                    username = user.username
            except Exception:
                pass

        # Record typing in presence module
        if self._presence:
            try:
                await run_in_threadpool(
                    self._presence.start_typing, connection.user_id, channel_id
                )
            except Exception:
                pass

        # Dispatch typing event to channel members
        await self._dispatch_typing_event(
            connection.user_id,
            channel_id,
            username,
            is_start=True,
            recipients=recipients,
            server_id=server_id,
        )

        return None, None, None

    async def handle_typing_stop(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle typing stop opcode - user stopped typing in a channel."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, None

        channel_id = data.get("channel_id")
        if not channel_id:
            return None, None, None

        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            return None, None, None

        assert connection.user_id is not None

        # Clear typing in presence module
        if self._presence:
            try:
                await run_in_threadpool(
                    self._presence.stop_typing, connection.user_id, channel_id
                )
            except Exception:
                pass

        # Dispatch typing stop event only to channel members
        recipients, server_id = await run_in_threadpool(
            _get_channel_recipients, connection.user_id, channel_id
        )
        if not recipients:
            return None, None, None

        await self._dispatch_typing_event(
            connection.user_id,
            channel_id,
            None,
            is_start=False,
            recipients=recipients,
            server_id=server_id,
        )

        return None, None, None

    async def _dispatch_presence_to_friends(
        self,
        user_id: int,
        status: str,
        custom_status: Optional[str] = None,
        custom_emoji: Optional[str] = None,
    ) -> None:
        """Dispatch presence update to friends and server members."""
        try:
            from src.api.routes.presence import (
                _get_presence_targets,
                _build_presence_event_data,
            )

            target_user_ids = _get_presence_targets(user_id)

            if not target_user_ids:
                return

            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                dispatcher = get_dispatcher()
                event = Event(
                    event_type=EventType.PRESENCE_UPDATE,
                    data=_build_presence_event_data(
                        user_id, status, custom_status, custom_emoji
                    ),
                )
                await dispatcher.dispatch_event(event, list(target_user_ids))
                logger.debug(
                    f"Dispatched presence update for user {user_id} to {len(target_user_ids)} users"
                )
        except Exception as e:
            logger.debug(f"Failed to dispatch presence: {e}")

    async def _dispatch_typing_event(
        self,
        user_id: int,
        channel_id: int,
        username: Optional[str],
        is_start: bool,
        recipients: Optional[List[int]] = None,
        server_id: Optional[int] = None,
    ) -> None:
        """Dispatch typing start/stop event to channel members."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if not ws_is_setup():
                return

            dispatcher = get_dispatcher()

            if recipients is None:
                recipients, server_id = await run_in_threadpool(
                    _get_channel_recipients, user_id, channel_id
                )

            if not recipients:
                return

            if is_start:
                event = Event(
                    event_type=EventType.TYPING_START,
                    data={
                        "channel_id": str(channel_id),
                        "user_id": str(user_id),
                        "username": username or "Someone",
                    },
                    server_id=server_id,
                    channel_id=channel_id,
                )
            else:
                event = Event(
                    event_type=EventType.TYPING_STOP,
                    data={
                        "channel_id": str(channel_id),
                        "user_id": str(user_id),
                    },
                    server_id=server_id,
                    channel_id=channel_id,
                )

            await dispatcher.dispatch_event(event, recipients)
        except Exception as e:
            logger.debug(f"Failed to dispatch typing event: {e}")
