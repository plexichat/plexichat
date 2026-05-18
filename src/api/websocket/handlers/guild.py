"""
Guild handlers - Handle guild-related opcodes (request guild members).
"""

from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING

import utils.logger as logger
import src.core.events as events_mod
from starlette.concurrency import run_in_threadpool

from src.api.websocket.connection import Connection
from src.api.websocket.opcodes import GatewayCloseCode

if TYPE_CHECKING:
    from src.core.servers.manager import ServerManager


class GuildHandler:
    """Handles guild-related opcodes."""

    def __init__(self, servers_module: Optional["ServerManager"] = None):
        """
        Initialize the guild handler.

        Args:
            servers_module: Servers module for guild data
        """
        self._servers: Optional["ServerManager"] = servers_module

    async def handle_request_guild_members(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle request guild members opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        guild_id = data.get("guild_id")
        if not guild_id:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            guild_id = int(guild_id)
        except (ValueError, TypeError):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            if not self._servers:
                return None, None, None

            # Check if user is a member of the guild
            try:
                server = await run_in_threadpool(
                    self._servers.get_server, guild_id, connection.user_id
                )
                if not server:
                    return None, None, None
            except Exception:
                return None, None, None

            # Get all members of the guild
            members = await run_in_threadpool(
                self._servers.get_members, connection.user_id, guild_id
            )

            if not members:
                return None, None, None

            # Build member list with user data
            import src.api as api

            auth = api.get_auth()
            member_list = []

            for m in members:
                u_id = m.user_id
                user = None
                if auth:
                    try:
                        user = await run_in_threadpool(auth.get_user, u_id)
                    except Exception:
                        pass

                status = "offline"
                presence_module = None
                try:
                    import src.api as api

                    presence_module = api.get_presence()
                except Exception:
                    pass
                if presence_module:
                    try:
                        presence_status = await run_in_threadpool(
                            presence_module.get_presence, u_id
                        )
                        if presence_status:
                            status = presence_status.status.value
                    except Exception:
                        pass

                member_list.append(
                    {
                        "user_id": str(u_id),
                        "username": user.username if user else f"User {u_id}",
                        "nickname": m.nickname,
                        "avatar_url": getattr(user, "avatar_url", None)
                        or getattr(m, "avatar_url", None),
                        "joined_at": m.joined_at,
                        "roles": [str(r) for r in (m.roles or [])],
                        "presence": {"status": status},
                    }
                )

            # Dispatch chunk
            from src.api.websocket import get_dispatcher

            dispatcher = get_dispatcher()

            event = events_mod.create_guild_members_chunk(
                server_id=guild_id, members=member_list, chunk_index=0, chunk_count=1
            )

            await dispatcher.dispatch_to_connection(connection, event)

        except Exception as e:
            logger.error(
                f"WS: Failed to process guild members request for {guild_id}: {e}",
                exc_info=True,
            )

        return None, None, None
