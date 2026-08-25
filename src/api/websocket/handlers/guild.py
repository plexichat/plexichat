"""
Guild handlers - Handle guild-related opcodes (request guild members).

Outcomes are always distinguishable for the client:

* success        -> one or more truthful GUILD_MEMBERS_CHUNK frames
                    (chunk_index/chunk_count reflect real pagination, an
                    empty guild yields a single empty chunk)
* denied         -> ERROR frame with code ``guild_members_denied``
                    (no existence/permission detail leaked)
* internal error -> ERROR frame with code ``internal_error`` + server log
"""

from typing import Optional, Dict, Any, Tuple, List, TYPE_CHECKING

import utils.logger as logger
import src.core.events as events_mod
from starlette.concurrency import run_in_threadpool

from src.api.websocket.connection import Connection
from src.api.websocket.opcodes import GatewayCloseCode

if TYPE_CHECKING:
    from src.core.servers.manager import ServerManager

# Page size matches the storage-layer default so each get_members call is a
# single bounded indexed query. _MAX_PAGES bounds the total work per request.
_CHUNK_SIZE = 100
_MAX_PAGES = 50


class GuildHandler:
    """Handles guild-related opcodes."""

    def __init__(self, servers_module: Optional["ServerManager"] = None):
        """
        Initialize the guild handler.

        Args:
            servers_module: Servers module for guild data
        """
        self._servers: Optional["ServerManager"] = servers_module

    async def _send_error(
        self,
        connection: Connection,
        code: str,
        message: Optional[str],
        guild_id: int,
    ) -> None:
        """Send a gateway ERROR frame to the requesting connection."""
        try:
            from src.api.websocket import get_dispatcher

            dispatcher = get_dispatcher()
            if dispatcher is not None:
                event = events_mod.create_gateway_error(
                    code=code, message=message, guild_id=guild_id
                )
                await dispatcher.dispatch_to_connection(connection, event)
            else:
                logger.warning(
                    f"WS: cannot send ERROR frame ({code}) for guild {guild_id}: "
                    f"no dispatcher configured"
                )
        except Exception as exc:
            logger.debug(f"WS: failed to send ERROR frame: {exc}")

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

        if not self._servers:
            logger.warning(
                "WS: REQUEST_GUILD_MEMBERS received but servers module is "
                "not configured; request ignored"
            )
            return None, None, None

        from src.core.servers.exceptions import (
            ServerAccessDeniedError,
            ServerNotFoundError,
        )

        user_id = connection.user_id

        # Membership gate. Denial vs internal failure are distinguished:
        # both not-found and not-a-member collapse into the SAME denial
        # code so existence is never leaked.
        try:
            await run_in_threadpool(self._servers.get_server, guild_id, user_id)
        except (ServerAccessDeniedError, ServerNotFoundError):
            await self._send_error(connection, "guild_members_denied", None, guild_id)
            return None, None, None
        except Exception as e:
            logger.error(
                f"WS: membership check failed for guild {guild_id}: {e}",
                exc_info=True,
            )
            await self._send_error(
                connection, "internal_error", "Membership check failed", guild_id
            )
            return None, None, None

        # Paginate members truthfully instead of emitting one fake chunk
        # that silently truncates guilds larger than the page size.
        members: List[Any] = []
        after_id = None
        truncated = False
        try:
            for _ in range(_MAX_PAGES):
                page = await run_in_threadpool(
                    self._servers.get_members, user_id, guild_id, _CHUNK_SIZE, after_id
                )
                if not page:
                    break
                members.extend(page)
                if len(page) < _CHUNK_SIZE:
                    break
                after_id = page[-1].id
            else:
                truncated = True
                logger.warning(
                    f"WS: member listing for guild {guild_id} hit the "
                    f"{_MAX_PAGES * _CHUNK_SIZE}-member request cap"
                )
        except Exception as e:
            logger.error(
                f"WS: failed to list members for guild {guild_id}: {e}",
                exc_info=True,
            )
            await self._send_error(
                connection, "internal_error", "Member listing failed", guild_id
            )
            return None, None, None

        # Build member entries with per-request caches so repeated
        # user/presence lookups stay O(distinct users), not O(lookups).
        import src.api as api

        auth = api.get_auth()
        presence_module = None
        try:
            presence_module = api.get_presence()
        except Exception:
            pass

        user_cache: Dict[int, Any] = {}
        status_cache: Dict[int, str] = {}

        member_list: List[Dict[str, Any]] = []
        for m in members:
            u_id = m.user_id

            if u_id not in user_cache:
                user_cache[u_id] = None
                if auth:
                    try:
                        user_cache[u_id] = await run_in_threadpool(auth.get_user, u_id)
                    except Exception:
                        pass

            if u_id not in status_cache:
                status_cache[u_id] = "offline"
                if presence_module:
                    try:
                        presence_status = await run_in_threadpool(
                            presence_module.get_presence, u_id
                        )
                        if presence_status:
                            status_cache[u_id] = presence_status.status.value
                    except Exception:
                        pass

            user = user_cache[u_id]
            member_list.append(
                {
                    "user_id": str(u_id),
                    "username": user.username if user else f"User {u_id}",
                    "nickname": m.nickname,
                    "avatar_url": getattr(user, "avatar_url", None)
                    or getattr(m, "avatar_url", None),
                    "joined_at": m.joined_at,
                    "roles": [str(r) for r in (m.roles or [])],
                    "presence": {"status": status_cache[u_id]},
                }
            )

        # Truthful chunking: an empty guild still produces ONE empty chunk
        # so clients can tell "empty" apart from denial/error/silence.
        from src.api.websocket import get_dispatcher

        dispatcher = get_dispatcher()
        if dispatcher is None:
            logger.warning(
                f"WS: cannot deliver member chunks for guild {guild_id}: "
                f"no gateway dispatcher configured"
            )
            return None, None, None

        total_chunks = max(1, (len(member_list) + _CHUNK_SIZE - 1) // _CHUNK_SIZE)
        for index in range(total_chunks):
            start = index * _CHUNK_SIZE
            chunk_members = member_list[start : start + _CHUNK_SIZE]
            event = events_mod.create_guild_members_chunk(
                server_id=guild_id,
                members=chunk_members,
                chunk_index=index,
                chunk_count=total_chunks,
            )
            try:
                await dispatcher.dispatch_to_connection(connection, event)
            except Exception as e:
                logger.error(
                    f"WS: failed to dispatch member chunk {index}/{total_chunks} "
                    f"for guild {guild_id}: {e}",
                    exc_info=True,
                )
                await self._send_error(
                    connection, "internal_error", "Chunk delivery failed", guild_id
                )
                return None, None, None

        if truncated:
            await self._send_error(
                connection,
                "guild_members_truncated",
                "Member list exceeded the per-request cap",
                guild_id,
            )

        return None, None, None
