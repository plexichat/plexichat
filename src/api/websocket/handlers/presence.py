"""
Presence handlers - Handle presence-related opcodes (presence_update, typing).
"""

from typing import Optional, Dict, Any, Tuple, List, TYPE_CHECKING
from collections import OrderedDict
import threading
import time

import utils.logger as logger
from starlette.concurrency import run_in_threadpool

from src.api.websocket.boundaries import parse_snowflake, validate_presence_payload
from src.api.websocket.connection import Connection
from src.api.websocket.opcodes import GatewayCloseCode
from src.core.presence.typing_audience import TypingAudienceCache

if TYPE_CHECKING:
    from src.core.presence.manager import PresenceManager

# F12 (hardened): a channel's typing audience is a per-channel property, not a
# per-sender one, so a typing burst in a large server used to re-run a
# per-member permission sweep for every frame. The resolved *viewer set* is
# cached per (server_id, channel_id) for a short TTL, turning subsequent events
# into a dict lookup. The cached value is sender-agnostic — it holds every user
# who may view the channel, and the current sender is removed at read time — so
# one resolution is safely shared by every sender without cross-user leakage.
# The cache is a bounded, epoch-expiring LRU (see TypingAudienceCache), so a
# flood of distinct channels cannot grow it without limit and a membership or
# permission change propagates within one TTL window.
_TYPING_AUDIENCE_TTL_S = 10.0
_MAX_TYPING_AUDIENCE_ENTRIES = 4096
_typing_audience_cache = TypingAudienceCache(
    max_entries=_MAX_TYPING_AUDIENCE_ENTRIES,
    ttl_seconds=_TYPING_AUDIENCE_TTL_S,
)


def invalidate_typing_audience_cache(
    server_id: Optional[int] = None, channel_id: Optional[int] = None
) -> int:
    """Drop warm typing audiences after a presence/membership change.

    Called on presence transitions (e.g. disconnect) so a stale viewer set
    cannot survive a write. Narrows to matching scopes when ids are supplied;
    with no arguments clears the whole process-local cache. Returns the number
    of entries removed.
    """
    return _typing_audience_cache.invalidate(server_id, channel_id)


# TYPING_START is emitted on every keystroke pause. Building its payload needs
# only the sender's ``username``, but the old path called ``auth.get_user`` for
# every frame: a threadpool round-trip plus a full ``User`` rebuild (encrypted
# field decryption + permission parsing) to read one string. The name is stable
# for minutes, so memoize it per user for a short window. Like the audience
# cache this is a bounded, epoch-expiring LRU: a flood of distinct senders
# cannot grow it without limit, and a rename propagates within one TTL.
_TYPING_USERNAME_TTL_S = 10.0
_MAX_TYPING_USERNAMES = 4096
_USERNAME_MISSING = object()


class _TypingUsernameCache:
    """Bounded, short-TTL LRU of ``user_id -> username`` for typing frames."""

    __slots__ = (
        "_max_entries",
        "_ttl",
        "_clock",
        "_entries",
        "_hits",
        "_misses",
        "_lock",
    )

    def __init__(
        self,
        max_entries: int = _MAX_TYPING_USERNAMES,
        ttl_seconds: float = _TYPING_USERNAME_TTL_S,
        clock=None,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._ttl = float(ttl_seconds)
        if self._ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._clock = clock or time.monotonic
        self._entries: "OrderedDict[tuple, str]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def _key(self, user_id: int) -> tuple:
        return (int(self._clock() / self._ttl), int(user_id))

    def get(self, user_id: int) -> Any:
        """Return the cached username (or ``_USERNAME_MISSING`` on a miss)."""
        key = self._key(user_id)
        with self._lock:
            value = self._entries.get(key, _USERNAME_MISSING)
            if value is not _USERNAME_MISSING:
                self._hits += 1
                self._entries.move_to_end(key)
            else:
                self._misses += 1
            return value

    def put(self, user_id: int, username: str) -> None:
        key = self._key(user_id)
        with self._lock:
            # Exact expiry: drop every entry from an earlier epoch on write.
            for stale in [k for k in self._entries if k[0] != key[0]]:
                self._entries.pop(stale, None)
            self._entries[key] = username
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
            }


_typing_username_cache = _TypingUsernameCache()


def get_typing_username_cache_stats() -> Dict[str, int]:
    """Expose username-memo hit/miss counters for observability and tests."""
    return _typing_username_cache.stats()


def _exclude_blocked_recipients(actor_id: int, recipients: List[int]) -> List[int]:
    """Drop recipients who have a block relationship with ``actor_id``.

    Typing frames are published with an *explicit* recipient list, so the
    gateway router's DM block filter (which only applies to implicit routing)
    never sees them. Server-channel viewers are resolved from ``channels.view``
    permission alone, and a blocked pair can still share a server -- without
    this gate a blocked user keeps receiving the other's typing frames.
    ``get_all_blocked_ids`` is the authoritative two-way block set. A failed
    lookup fails closed: withhold the whole audience. Applied per actor at read
    time (like sender exclusion) so the shared per-channel cache stays
    sender-agnostic.
    """
    if not recipients:
        return recipients
    import src.api as api

    relationships = api.get_relationships()
    if not relationships:
        return recipients
    # An uninitialized relationships module has no block data at all (the
    # optional module is simply not wired in this deployment). Match the DM
    # helper: leave the audience unchanged so typing still works, rather than
    # treating "no source" as a failed lookup. Once wired, a lookup failure
    # below fails closed.
    if (
        getattr(relationships, "_setup_complete", None) is False
        and getattr(relationships, "_manager", None) is None
    ):
        return recipients
    try:
        blocked_ids = {int(x) for x in relationships.get_all_blocked_ids(actor_id)}
    except Exception as e:
        logger.debug(f"typing audience block lookup failed: {e}")
        return []
    return [uid for uid in recipients if int(uid) not in blocked_ids]


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
                    viewers = _typing_audience_cache.get(int(sid), int(channel_id))
                    if viewers is not None:
                        # Warm hit: strip the current sender at read time. The
                        # cached set is shared across senders, so baking one
                        # sender's exclusion into it would leak their audience
                        # into another user's typing event. Apply the two-way
                        # block filter here too, for the same reason.
                        audience = [
                            uid for uid in viewers if int(uid) != int(user_id)
                        ]
                        return _exclude_blocked_recipients(user_id, audience), server_id

                    # Cold path: resolve the FULL set of users who may view the
                    # channel. Deliberately do NOT exclude ``user_id`` here —
                    # keeping the value sender-agnostic is what makes one cache
                    # entry safe to reuse for every sender in a burst.
                    member_user_ids = servers_mod.get_member_user_ids(sid)
                    has_perm = getattr(servers_mod, "has_permission", None)
                    allowed: List[int] = []
                    for member_user_id in member_user_ids:
                        try:
                            if callable(has_perm):
                                if has_perm(
                                    member_user_id, sid, "channels.view", channel_id
                                ):
                                    allowed.append(member_user_id)
                            elif servers_mod.get_channel(channel_id, member_user_id):
                                allowed.append(member_user_id)
                        except Exception as member_error:
                            logger.debug(
                                f"typing audience member check failed: {member_error}"
                            )
                            continue

                    _typing_audience_cache.put(int(sid), int(channel_id), allowed)
                    user_ids = [uid for uid in allowed if int(uid) != int(user_id)]
        except Exception as audience_error:
            logger.debug(f"typing audience resolution failed: {audience_error}")

    if not user_ids:
        messaging = api.get_messaging()
        if messaging:
            try:
                participants = messaging.get_participants(user_id, channel_id)
                if participants:
                    user_ids = [p.user_id for p in participants if p.user_id != user_id]
            except Exception as participant_error:
                logger.debug(
                    f"typing conversation participants failed: {participant_error}"
                )

    return _exclude_blocked_recipients(user_id, user_ids), server_id


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

        try:
            fields = validate_presence_payload(data)
        except ValueError as exc:
            logger.warning(
                f"User {connection.user_id} sent invalid presence payload: {exc}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)
        status = fields["status"]
        custom_status = fields["custom_status"]
        custom_emoji = fields["custom_emoji"]
        activities = fields["activities"]

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

        # A bare int() accepted negatives and arbitrarily large ids, which
        # then reached the channel/participant lookups and the fan-out. Bound
        # the id at the gateway boundary like every other snowflake.
        try:
            channel_id = parse_snowflake(channel_id, "channel_id")
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

        # Resolve the sender's username for the typing event. A burst emits one
        # frame per keystroke pause; resolving the name on every frame paid a
        # threadpool hop plus a full ``User`` rebuild just to read one string.
        # Memoize per user for a short bounded window (see
        # ``_typing_username_cache``) so the burst resolves it once.
        username = _typing_username_cache.get(connection.user_id)
        if username is _USERNAME_MISSING:
            username = None
            import src.api as api

            auth = api.get_auth()
            if auth:
                try:
                    user = await run_in_threadpool(auth.get_user, connection.user_id)
                    if user and user.username:
                        username = user.username
                        _typing_username_cache.put(connection.user_id, username)
                except Exception as user_error:
                    logger.debug(f"typing user lookup failed: {user_error}")

        # Record typing in presence module
        if self._presence:
            try:
                await run_in_threadpool(
                    self._presence.start_typing, connection.user_id, channel_id
                )
            except Exception as typing_error:
                logger.debug(f"presence start_typing failed: {typing_error}")

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

        # A bare int() accepted negatives and arbitrarily large ids, which
        # then reached the channel/participant lookups and the fan-out. Bound
        # the id at the gateway boundary like every other snowflake.
        try:
            channel_id = parse_snowflake(channel_id, "channel_id")
        except (ValueError, TypeError):
            return None, None, None

        assert connection.user_id is not None

        # Clear typing in presence module
        if self._presence:
            try:
                await run_in_threadpool(
                    self._presence.stop_typing, connection.user_id, channel_id
                )
            except Exception as typing_error:
                logger.debug(f"presence stop_typing failed: {typing_error}")

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
