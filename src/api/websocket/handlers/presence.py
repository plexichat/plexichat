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
            assert connection.user_id is not None
            await run_in_threadpool(
                self._presence.set_status, connection.user_id, status_enum
            )

            if activities:
                activity = activities[0]
                from src.core.presence import ActivityType

                activity_type = ActivityType(activity.get("type", "custom"))
                await run_in_threadpool(
                    self._presence.set_activity,
                    connection.user_id,
                    activity_type,
                    activity.get("name", ""),
                    details=activity.get("details"),
                    state=activity.get("state"),
                )

            # Dispatch presence update to friends
            assert connection.user_id is not None
            await self._dispatch_presence_to_friends(
                connection.user_id,
                status if status != "invisible" else "offline",
                custom_status,
                custom_emoji,
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
            connection.user_id, channel_id, username, is_start=True
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

        # Dispatch typing stop event to channel members
        await self._dispatch_typing_event(
            connection.user_id, channel_id, None, is_start=False
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
            from src.api.routes.presence import _get_presence_targets

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
                    data={
                        "user_id": str(user_id),
                        "status": status,
                        "custom_status": custom_status,
                        "custom_emoji": custom_emoji,
                    },
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
    ) -> None:
        """Dispatch typing start/stop event to channel members."""
        try:
            import src.api as api
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if not ws_is_setup():
                return

            dispatcher = get_dispatcher()
            user_ids: List[int] = []
            server_id: Optional[int] = None

            # Try to get channel members from servers module
            servers_mod = api.get_servers()
            if servers_mod:
                try:
                    channel = await run_in_threadpool(
                        servers_mod.get_channel, channel_id, user_id
                    )
                    if channel:
                        server_id = getattr(channel, "server_id", None)
                        if server_id:
                            user_ids = await run_in_threadpool(
                                self._get_typing_recipient_ids,
                                user_id,
                                channel_id,
                                server_id,
                            )
                except Exception:
                    pass

            # If not a server channel, try DM
            if not user_ids:
                messaging = api.get_messaging()
                if messaging:
                    try:
                        participants = await run_in_threadpool(
                            messaging.get_participants, user_id, channel_id
                        )
                        if participants:
                            user_ids = [
                                p.user_id for p in participants if p.user_id != user_id
                            ]
                    except Exception:
                        pass

            if not user_ids:
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

            await dispatcher.dispatch_event(event, user_ids)
        except Exception as e:
            logger.debug(f"Failed to dispatch typing event: {e}")

    def _get_typing_recipient_ids(
        self, user_id: int, channel_id: int, server_id: int, include_self: bool = False
    ) -> List[int]:
        """Return only members who can still view a channel."""
        import src.api as api

        servers_mod = api.get_servers()
        if not servers_mod:
            return []

        member_user_ids = servers_mod.get_member_user_ids(
            server_id, exclude_user_id=None if include_self else user_id
        )

        visible_user_ids: List[int] = []
        for member_user_id in member_user_ids:
            try:
                if servers_mod.get_channel(channel_id, member_user_id):
                    visible_user_ids.append(member_user_id)
            except Exception:
                continue

        return visible_user_ids
