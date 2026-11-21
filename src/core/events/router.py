"""
Event routing - Logic for determining which users receive which events.
"""

from typing import Callable, Dict, Mapping, Optional, List, Set

import utils.logger as logger

from .recipient_cache import RecipientResolutionCache
from .types import EventType, GatewayIntent
from .models import Event


# Presence updates are the highest-frequency scoped event: every status tick
# re-resolves the subject's whole audience (friend graph + the member list of
# every server they are in + a block filter). Memoize the resolved audience for
# a short epoch so a burst pays for one walk. The cache is bounded, copy-safe
# and keyed by the complete resolution identity -- see
# ``EventRouter._get_presence_recipients`` -- so one user's audience can never
# be served for another's, and a source/router swap always re-resolves.
_presence_audience_cache = RecipientResolutionCache()


def clear_presence_audience_cache() -> None:
    """Invalidate all cached presence audiences (setup, membership changes, tests)."""
    _presence_audience_cache.clear()


def get_presence_audience_cache_stats() -> Dict[str, int]:
    """Expose hit/miss/size counters for observability and regression tests."""
    return _presence_audience_cache.stats()


def _scope_id(value):
    """Normalize a scope id for the presence cache key without accepting bools."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        # Cache keys must be hashable; stringify unexpected id types so a
        # malformed id can never raise out of the routing hot path.
        return str(value)


# DM/group event audiences were the last gateway audience still resolved from
# scratch on every event: ``_get_dm_participant_ids`` lists every conversation
# participant and resolves a two-way block set, so a burst of messages/typing/
# reactions in the same conversation repeats the whole scan. Memoize the fully
# filtered audience in the same bounded, copy-safe, short-TTL primitive used
# for presence. The key is the complete resolution identity -- channel, acting
# user (block exclusions depend on it), audience sources and router -- so one
# conversation/actor can never reuse another's recipients, and a source/router
# swap always re-resolves. Fail-closed empty results are cached too: withholding
# for one TTL window is safer than widening an audience to a blocked party.
_dm_participant_cache = RecipientResolutionCache()


def clear_dm_participant_cache() -> None:
    """Invalidate all cached DM participant audiences (setup, tests)."""
    _dm_participant_cache.clear()


def get_dm_participant_cache_stats() -> Dict[str, int]:
    """Expose hit/miss/size counters for observability and regression tests."""
    return _dm_participant_cache.stats()


# Hard ceiling on the number of recipients one event may resolve to. The
# recipient list is a shared input to every delivery lane: critical
# subscribers are invoked with it on the emitting request thread, the bounded
# async queue stores it by value, and ``EventManager._publish_cross_worker``
# serialises the whole list into the Valkey pub/sub payload that every sibling
# worker re-fans out. Without a ceiling here, one caller-supplied ``user_ids``
# list (or one @everyone-sized server) amplifies into unbounded work and memory
# on every worker. Bounding at the router is the single choke point that
# protects all of those downstream fans. Aligned with
# ``EventManager._DISPATCH_QUEUE_MAX`` so a single event cannot dominate a
# full queue.
DEFAULT_MAX_RECIPIENTS = 4096


EVENT_INTENT_MAP = {
    EventType.GUILD_CREATE: GatewayIntent.GUILDS,
    EventType.GUILD_UPDATE: GatewayIntent.GUILDS,
    EventType.GUILD_DELETE: GatewayIntent.GUILDS,
    EventType.GUILD_ROLE_CREATE: GatewayIntent.GUILDS,
    EventType.GUILD_ROLE_UPDATE: GatewayIntent.GUILDS,
    EventType.GUILD_ROLE_DELETE: GatewayIntent.GUILDS,
    EventType.CHANNEL_CREATE: GatewayIntent.GUILDS,
    EventType.CHANNEL_UPDATE: GatewayIntent.GUILDS,
    EventType.CHANNEL_DELETE: GatewayIntent.GUILDS,
    EventType.CHANNEL_PINS_UPDATE: GatewayIntent.GUILDS,
    EventType.THREAD_CREATE: GatewayIntent.GUILDS,
    EventType.THREAD_UPDATE: GatewayIntent.GUILDS,
    EventType.THREAD_DELETE: GatewayIntent.GUILDS,
    EventType.GUILD_MEMBER_ADD: GatewayIntent.GUILD_MEMBERS,
    EventType.GUILD_MEMBER_REMOVE: GatewayIntent.GUILD_MEMBERS,
    EventType.GUILD_MEMBER_UPDATE: GatewayIntent.GUILD_MEMBERS,
    EventType.GUILD_MEMBERS_CHUNK: GatewayIntent.GUILD_MEMBERS,
    EventType.GUILD_BAN_ADD: GatewayIntent.GUILD_BANS,
    EventType.GUILD_BAN_REMOVE: GatewayIntent.GUILD_BANS,
    EventType.GUILD_EMOJIS_UPDATE: GatewayIntent.GUILD_EMOJIS,
    EventType.GUILD_STICKERS_UPDATE: GatewayIntent.GUILD_EMOJIS,
    EventType.WEBHOOKS_UPDATE: GatewayIntent.GUILD_WEBHOOKS,
    EventType.INVITE_CREATE: GatewayIntent.GUILD_INVITES,
    EventType.INVITE_DELETE: GatewayIntent.GUILD_INVITES,
    EventType.VOICE_STATE_UPDATE: GatewayIntent.GUILD_VOICE_STATES,
    EventType.PRESENCE_UPDATE: GatewayIntent.GUILD_PRESENCES,
    EventType.MESSAGE_REACTION_ADD: GatewayIntent.GUILD_MESSAGE_REACTIONS,
    EventType.MESSAGE_REACTION_REMOVE: GatewayIntent.GUILD_MESSAGE_REACTIONS,
    EventType.MESSAGE_REACTION_REMOVE_ALL: GatewayIntent.GUILD_MESSAGE_REACTIONS,
    EventType.MESSAGE_REACTION_REMOVE_EMOJI: GatewayIntent.GUILD_MESSAGE_REACTIONS,
    # A poll is attached to a message, so poll changes are message activity.
    # Without this entry a guild-scoped POLL_UPDATE fell through the map to
    # "no intent required" and reached sockets with no messaging intent.
    EventType.POLL_UPDATE: GatewayIntent.GUILD_MESSAGES,
    EventType.TYPING_START: GatewayIntent.GUILD_MESSAGE_TYPING,
    # TYPING_STOP is the other half of the same gateway pair (the gateway
    # dispatches it from ``clear_all_typing`` and the presence handler), so
    # it owes the same intent as TYPING_START. Without an entry here
    # ``filter_by_intents`` falls through to "no intent required" and every
    # client received typing-stop frames whether or not it asked for the
    # typing intent, while the matching TYPING_START was correctly filtered.
    EventType.TYPING_STOP: GatewayIntent.GUILD_MESSAGE_TYPING,
    EventType.ARTIFACT_CREATE: GatewayIntent.ARTIFACTS,
    EventType.ARTIFACT_UPDATE: GatewayIntent.ARTIFACTS,
    EventType.ARTIFACT_DELETE: GatewayIntent.ARTIFACTS,
    EventType.ARTIFACT_OP: GatewayIntent.ARTIFACTS,
    EventType.WORKBOOK_UPDATE: GatewayIntent.SUMS,
    EventType.SUM_OPERATION: GatewayIntent.SUMS,
    EventType.SUM_PRESENCE: GatewayIntent.SUMS,
}

DM_EVENT_INTENT_MAP = {
    EventType.MESSAGE_CREATE: GatewayIntent.DIRECT_MESSAGES,
    EventType.MESSAGE_UPDATE: GatewayIntent.DIRECT_MESSAGES,
    EventType.MESSAGE_DELETE: GatewayIntent.DIRECT_MESSAGES,
    # Bulk delete is the DM twin of MESSAGE_DELETE: emit_message_delete_bulk
    # emits it with ``server_id=None`` for DM channels, so without this entry
    # the router classified it as a DM event, found no DM intent, and delivered
    # it unconditionally -- leaking the ids of messages deleted in a private
    # conversation to a client that never requested DIRECT_MESSAGES. Its guild
    # gate is unchanged (GUILD_MESSAGE_EVENTS -> GUILD_MESSAGES).
    EventType.MESSAGE_DELETE_BULK: GatewayIntent.DIRECT_MESSAGES,
    # emit_poll_update stamps a channel_id but never a server_id, so every
    # poll update is DM-classified by the router. With no DM entry it hit the
    # default-allow and disclosed a conversation's poll/message activity to
    # connections that never requested DIRECT_MESSAGES.
    EventType.POLL_UPDATE: GatewayIntent.DIRECT_MESSAGES,
    EventType.MESSAGE_REACTION_ADD: GatewayIntent.DIRECT_MESSAGE_REACTIONS,
    EventType.MESSAGE_REACTION_REMOVE: GatewayIntent.DIRECT_MESSAGE_REACTIONS,
    # DM twin of the guild map's REMOVE_ALL entry: ``clear_reactions`` emits
    # this with no guild_id, so a DM wipe is DM-scoped. Without this entry the
    # router's default-allow for unmapped DM events delivered it to connections
    # that never requested DIRECT_MESSAGE_REACTIONS (only the guild fallback
    # gate checked GUILD_MESSAGE_REACTIONS), and withheld it from clients that
    # did request the DM reaction intent.
    EventType.MESSAGE_REACTION_REMOVE_ALL: GatewayIntent.DIRECT_MESSAGE_REACTIONS,
    EventType.MESSAGE_REACTION_REMOVE_EMOJI: GatewayIntent.DIRECT_MESSAGE_REACTIONS,
    EventType.TYPING_START: GatewayIntent.DIRECT_MESSAGE_TYPING,
    # DM twin of the guild mapping above: a DM typing stop must be gated by
    # the DM typing intent, exactly as the DM typing start is.
    EventType.TYPING_STOP: GatewayIntent.DIRECT_MESSAGE_TYPING,
}

GUILD_MESSAGE_EVENTS = {
    EventType.MESSAGE_CREATE,
    EventType.MESSAGE_UPDATE,
    EventType.MESSAGE_DELETE,
    EventType.MESSAGE_DELETE_BULK,
}


def get_required_intent(event_type: EventType) -> Optional[GatewayIntent]:
    """
    Get the required intent for an event type.

    Args:
        event_type: The event type

    Returns:
        Required intent or None if no intent required
    """
    return EVENT_INTENT_MAP.get(event_type)


def get_dm_intent(event_type: EventType) -> Optional[GatewayIntent]:
    """
    Get the required intent for a DM event type.

    Args:
        event_type: The event type

    Returns:
        Required DM intent or None
    """
    return DM_EVENT_INTENT_MAP.get(event_type)


def filter_by_intents(event: Event, intents: int) -> bool:
    """
    Check if an event passes intent filtering.

    Args:
        event: Event to check
        intents: User's intent flags

    Returns:
        True if event should be sent to user
    """
    is_dm = event.server_id is None and event.channel_id is not None

    if is_dm:
        required = get_dm_intent(event.event_type)
        if required is not None:
            return bool(intents & required)
        return True

    if event.event_type in GUILD_MESSAGE_EVENTS:
        if not (intents & GatewayIntent.GUILD_MESSAGES):
            return False
        return True

    required = get_required_intent(event.event_type)
    if required is not None:
        return bool(intents & required)

    return True


class EventRouter:
    """Routes events to appropriate users based on context."""

    # Class attribute so tests / large deployments can raise the ceiling
    # without changing code. See ``DEFAULT_MAX_RECIPIENTS`` for rationale.
    MAX_RECIPIENTS = DEFAULT_MAX_RECIPIENTS

    def __init__(
        self,
        relationships_module=None,
        servers_module=None,
        messaging_module=None,
    ):
        """
        Initialize the event router.

        Args:
            relationships_module: For friend/block relationships
            servers_module: For server membership
            messaging_module: For DM participants
        """
        self._relationships = relationships_module
        self._servers = servers_module
        self._messaging = messaging_module
        # Observability: how many resolutions hit the recipient ceiling.
        # Surfaced via ``get_routing_metrics`` for health/dashboards.
        self._truncated_recipient_fanouts = 0

    def _bounded_unique_recipients(self, recipients) -> List[int]:
        """Dedupe (preserving first-seen order) and hard-cap recipients.

        Every delivery lane downstream of routing re-iterates this list, so two
        invariants matter here:

        * **dedupe** — an explicit ``user_ids`` list may repeat an id; without
          collapsing it the same user receives the event multiple times, and
          the duplicate is amplified across every sibling worker by the
          cross-worker pub/sub fan-out.
        * **bound** — both the accumulator and the seen-set are capped at
          ``MAX_RECIPIENTS``, so even an adversarial multi-million-id input
          cannot grow memory without limit. We stop at the first unique id
          beyond the cap (a conservative "there was more" signal for the
          warning) rather than materialising the full input first.
        """
        limit = max(1, int(self.MAX_RECIPIENTS))
        seen: Set[int] = set()
        ordered: List[int] = []
        truncated = False
        for uid in recipients:
            if uid in seen:
                continue
            if len(ordered) >= limit:
                truncated = True
                break
            seen.add(uid)
            ordered.append(uid)
        if truncated:
            self._truncated_recipient_fanouts += 1
            logger.warning(
                "Event recipient fan-out hit MAX_RECIPIENTS=%d; an event was "
                "truncated to keep cross-worker/local fan-out bounded "
                "(total truncations: %d)",
                limit,
                self._truncated_recipient_fanouts,
            )
        return ordered

    def get_routing_metrics(self) -> dict:
        """Expose recipient-fan-out bounds for observability."""
        return {
            "max_recipients": self.MAX_RECIPIENTS,
            "truncated_recipient_fanouts": self._truncated_recipient_fanouts,
        }

    def get_recipients(
        self,
        event: Event,
        user_ids: Optional[List[int]] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        exclude_user_ids: Optional[List[int]] = None,
    ) -> List[int]:
        """
        Determine which users should receive an event.

        Args:
            event: Event to route
            user_ids: Explicit user IDs (overrides routing)
            server_id: Server ID for server events
            channel_id: Channel ID for channel events
            exclude_user_ids: Users to exclude

        Returns:
            List of user IDs to receive the event
        """
        exclude_set: Set[int] = set(exclude_user_ids or [])

        if user_ids is not None:
            # Caller-supplied audiences (e.g. gateway emitters, workbook
            # updates) are the main unbounded-work vector: dedupe + cap before
            # the list reaches any delivery lane.
            return self._bounded_unique_recipients(
                uid for uid in user_ids if uid not in exclude_set
            )

        recipients: Set[int] = set()

        effective_server_id = server_id or event.server_id
        effective_channel_id = channel_id or event.channel_id

        if effective_server_id:
            # A server-scoped event may ONLY be widened to the server's
            # members. The old ``if ... and self._servers / elif`` shape
            # fell through to DM-participant resolution whenever the
            # servers module was missing, silently rerouting guild content
            # to a conversation's participant list — an audience leak.
            # Without the authoritative member source, resolve nobody.
            if self._servers:
                recipients.update(self._get_server_member_ids(effective_server_id))
        elif effective_channel_id and self._messaging:
            recipients.update(
                self._get_dm_participant_ids(
                    effective_channel_id, acting_user_id=event.user_id
                )
            )

        if event.event_type == EventType.PRESENCE_UPDATE:
            if event.user_id and self._relationships:
                recipients.update(self._get_presence_recipients(event.user_id))

        # Server/DM/presence resolution already dedupes via a set, but it has
        # no size ceiling: a large server or friend graph would otherwise hand
        # every downstream lane an unbounded list. Apply the same bound.
        return self._bounded_unique_recipients(
            uid for uid in recipients if uid not in exclude_set
        )

    def _get_server_member_ids(self, server_id: int) -> List[int]:
        """Get all member user IDs for a server."""
        if not self._servers:
            return []
        # ServerManager has get_member_user_ids
        members = self._servers.get_member_user_ids(server_id)
        return members if members else []

    def _get_dm_participant_ids(
        self, channel_id: int, acting_user_id: Optional[int] = None
    ) -> List[int]:
        """Get participant user IDs for a DM/group conversation (cached).

        See ``_resolve_dm_participant_ids`` for the resolution and block-filter
        semantics. The resolved audience is memoized for a short epoch in a
        bounded, copy-safe cache keyed by the complete resolution identity, so
        a burst of events in the same conversation pays for one scan. The acting
        user is part of the key because the two-way block exclusions depend on
        it; a different actor therefore never reuses another's filtered list.
        """
        cache_key = (
            _scope_id(channel_id),
            _scope_id(acting_user_id),
            id(self._messaging),
            id(self._relationships),
            id(self),
        )
        cached = _dm_participant_cache.get(cache_key)
        if cached is not None:
            return cached
        result = self._resolve_dm_participant_ids(channel_id, acting_user_id)
        _dm_participant_cache.put(cache_key, result)
        return result

    def _resolve_dm_participant_ids(
        self, channel_id: int, acting_user_id: Optional[int] = None
    ) -> List[int]:
        """Get participant user IDs for a DM/group conversation.

        Participants involved in a block (in either direction) with another
        participant never receive the conversation's events, mirroring the
        ``get_all_blocked_ids`` exclusion applied to presence updates. When
        ``acting_user_id`` is known (e.g. the message author), that user is
        always kept so their own actions still echo back to them.
        """
        if not self._messaging:
            return []
        participants = self._messaging.get_participant_ids(channel_id)
        if not participants:
            return []

        if not self._relationships:
            return participants

        participant_set = set(participants)
        excluded: Set[int] = set()

        # A group DM can hold many participants. Resolving the two-way block
        # set with one cached lookup per participant is an N+1 fan-out on cold
        # caches (and a burst of Valkey GETs even when warm). The relationship
        # manager exposes a bounded, chunked bulk helper that returns the very
        # same sets in one call; fall back to per-id lookups only for legacy
        # stubs that do not implement it.
        # Typed explicitly: the attribute is optional, so ``getattr`` returns
        # ``object | None`` and the call result would be ``object``, which has
        # no ``items()`` to iterate. The signature is the one the relationship
        # manager implements; the compatibility path below covers stubs that do
        # not expose it at all.
        bulk_lookup: Optional[Callable[[List[int]], Mapping[int, Set[int]]]] = getattr(
            self._relationships, "get_all_blocked_ids_bulk", None
        )
        if callable(bulk_lookup):
            try:
                blocked_by_id = {
                    int(uid): values
                    for uid, values in bulk_lookup(participants).items()
                }
            except Exception as e:
                logger.debug(
                    f"Failed to resolve bulk blocked IDs for channel {channel_id}: {e}"
                )
                # Fail closed: without the authoritative block sets we cannot
                # prove any participant is safe to receive this event, so
                # deliver to nobody rather than risk leaking to a blocked pair.
                return []
        else:
            blocked_by_id = {}
            for pid in participants:
                try:
                    blocked_by_id[int(pid)] = self._relationships.get_all_blocked_ids(
                        pid
                    )
                except Exception as e:
                    logger.debug(
                        f"Failed to resolve blocked IDs for {pid} on channel "
                        f"{channel_id}: {e}"
                    )
                    # Same fail-closed rule on the compatibility path.
                    return []

        for pid in participants:
            for bid in blocked_by_id.get(int(pid)) or []:
                bid = int(bid)
                if bid not in participant_set or bid == pid:
                    continue
                # pid and bid must not see each other's events in this DM.
                if acting_user_id is not None:
                    if pid == acting_user_id:
                        excluded.add(bid)
                    elif bid == acting_user_id:
                        excluded.add(pid)
                else:
                    excluded.add(bid)
                    excluded.add(pid)

        if not excluded:
            return participants
        return [pid for pid in participants if pid not in excluded]

    def _get_presence_recipients(self, user_id: int) -> List[int]:
        """Get users who should receive presence updates for a user.

        Presence is emitted on every status change, so this audience -- the
        subject's friends plus the full member list of each server they are in,
        minus blocked users -- was being rebuilt for every single event. A user
        in several large servers turns that into thousands of member lookups
        per burst. The result is memoized in a bounded, short-TTL cache keyed by
        the *complete* resolution identity:

        * the subject user id (two users never share an audience);
        * the identities of the relationships and servers sources (a re-setup
          of either cannot replay a stale audience);
        * this router's identity (a fresh router from ``events.setup()`` starts
          with a fresh namespace).

        The cache returns lists by value, so a caller mutating its copy cannot
        corrupt a later reuse. Fail-closed results (empty audiences) are cached
        too: withholding for one TTL window is safer than widening an audience.
        """
        cache_key = (
            _scope_id(user_id),
            id(self._relationships),
            id(self._servers),
            id(self),
        )
        cached = _presence_audience_cache.get(cache_key)
        if cached is not None:
            return cached

        recipients: Set[int] = set()

        if self._relationships:
            friend_ids = self._relationships.get_friend_ids(user_id)
            if friend_ids:
                recipients.update(friend_ids)

        if self._servers:
            # The shared members of every server the subject belongs to are
            # exactly the union of each server's member list. Walking the
            # server ids and issuing one SELECT per server was an N+1 that
            # grew with the subject's server count on every cold resolution.
            # The manager exposes one indexed self-join for that union; use it
            # when available and keep the loop only as a fallback for legacy
            # sources that do not implement the helper.
            bulk_lookup: Optional[Callable[[int], List[int]]] = getattr(
                self._servers, "get_all_shared_member_ids", None
            )
            if callable(bulk_lookup):
                try:
                    recipients.update(bulk_lookup(user_id) or [])
                except Exception as e:
                    # Fail closed: without the authoritative shared-member set
                    # we cannot prove the audience is complete, so withhold the
                    # event rather than widening it to friends only. Cache the
                    # empty result too, like every other fail-closed resolution.
                    logger.debug(
                        f"Failed bulk shared-member lookup for user {user_id}: {e}"
                    )
                    result: List[int] = []
                    _presence_audience_cache.put(cache_key, result)
                    return result
            else:
                server_ids = self._servers.get_user_server_ids(user_id)
                for sid in server_ids or []:
                    member_ids = self._get_server_member_ids(sid)
                    recipients.update(member_ids)

        recipients.discard(user_id)

        # Filter out blocked users
        if self._relationships and recipients:
            blocked_ids = self._relationships.get_all_blocked_ids(user_id)
            for bid in blocked_ids:
                recipients.discard(bid)

        result = list(recipients)
        _presence_audience_cache.put(cache_key, result)
        return result
