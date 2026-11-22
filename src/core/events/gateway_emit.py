"""
Gateway event emission helpers - Shared module for dispatching
real-time gateway events to connected clients.

This module centralizes emission of every client-facing gateway event,
especially the ones that were previously defined-only (never emitted) in
the :class:`src.core.events.types.EventType` enum. Routes and core modules
should call these helpers instead of constructing :class:`Event` objects and
calling the dispatcher directly, so that routing and intent filtering stay
consistent in one place.

All helpers are intentionally non-raising: a failure to dispatch must never
break the underlying REST/CRUD operation that triggered it.
"""

from typing import Any, Callable, Dict, List, Mapping, Optional, ParamSpec, cast

import functools
import sys

import utils.logger as logger

from .recipient_cache import RecipientResolutionCache
from .snowflakes import stringify_snowflake_ids
from .types import EventType

P = ParamSpec("P")


# Shared, bounded cache of fully resolved channel-event audiences. A burst of
# typing / reaction / read-receipt frames for one channel reuses a single
# resolution instead of re-walking every member's channels.view permission on
# every emit. The key includes the complete resolution identity and the
# audience-source module identities (see resolve_channel_event_recipients), so
# no audience can be served another audience's recipients.
_channel_recipient_cache = RecipientResolutionCache()


def clear_channel_recipient_cache() -> None:
    """Invalidate all cached channel-event audiences.

    Called by the events test conftest between tests and available to setup /
    membership-change code that wants an immediate, unconditional flush.
    """
    _channel_recipient_cache.clear()


def get_channel_recipient_cache_stats() -> Dict[str, int]:
    """Expose hit/miss/size counters for observability and regression tests."""
    return _channel_recipient_cache.stats()


def _non_raising(fn: Callable[P, None]) -> Callable[P, None]:
    """Guard a gateway emitter so payload building cannot raise.

    ``_emit`` already swallows dispatch errors, but ``int(...)``/``str(...)``
    coercions run while building the payload dict -- outside that guard. A
    malformed id (e.g. ``user["id"] = "abc"`` or a bad ``revision``) would
    then raise into the REST handler, violating this module's non-raising
    contract. The decorator keeps the guarantee for current and future
    helpers: log at WARNING (so breakage is visible) and return None.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"gateway_emit: failed to build {fn.__name__}: {e}")
            return None

    return wrapper


def _emit(
    event_type: EventType,
    data: Dict[str, Any],
    *,
    user_ids: Optional[List[int]] = None,
    server_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    exclude_user_ids: Optional[List[int]] = None,
) -> None:
    """Dispatch a gateway event, swallowing errors so callers stay safe."""
    try:
        from src.core import events

        if not events.is_setup():
            return
        # Stamp the authoritative scope onto the Event itself, not just the
        # dispatch kwargs. Intent filtering happens on the Event object (both
        # for local subscribers and, via the cross-worker envelope, on sibling
        # workers). A scope-less Event made every one of these guild/DM events
        # classify as guild-scoped, applying the wrong intent gate. Routing is
        # unchanged: ``dispatch`` already receives the same scope kwargs.
        events.dispatch(
            events.Event(
                event_type=event_type,
                data=data,
                server_id=server_id,
                channel_id=channel_id,
            ),
            user_ids=user_ids,
            server_id=server_id,
            channel_id=channel_id,
            exclude_user_ids=exclude_user_ids,
        )
    except Exception as e:  # pragma: no cover - defensive
        # C1: a serialization or routing bug in ANY emitter was previously
        # invisible at default log levels (DEBUG). Surface it at WARNING so
        # broken emissions are detectable while staying non-raising.
        logger.warning(f"gateway_emit: failed to dispatch {event_type.value}: {e}")


# Gateway payload id fields are stringified centrally via
# ``events.snowflakes`` so this rule cannot drift between emitters.


def _coerce_routing_id(value: Any) -> Optional[int]:
    """Coerce a routing id back to int for DB-backed dispatch routing.

    Gateway payload builders carry ids as decimal strings (JS-safe), but
    routing queries need ints. ``None`` stays ``None``; unparseable values
    return ``None`` so routing falls back to event-scoped defaults.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _is_private_thread_type(value: Any) -> bool:
    """True only when a thread_type explicitly marks the thread private.

    ThreadType is a str-valued enum ("public"/"private"/"announcement").
    Enum members, their ``.value`` and the raw string are all accepted.
    """
    if isinstance(value, bool):
        return False
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, str):
        return value.strip().lower() == "private"
    return False


def _is_known_public_thread_type(value: Any) -> bool:
    """True only for an explicit, recognized non-private thread type."""
    if isinstance(value, bool):
        return False
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, str):
        return value.strip().lower() in ("public", "announcement")
    return False


def _thread_privacy(thread_id: Optional[int], claimed_type: Any) -> bool:
    """Whether a thread event must be treated as private (fail closed).

    Resolution order:

    1. The authoritative ``threads.get_thread_type_value(thread_id)`` when the
       threads module is available. That lookup deliberately sees soft-deleted
       (tombstoned) rows, so THREAD_DELETE can still classify its own thread.
    2. The type carried on the caller's payload/argument.

    Fail-closed rule: the event is audience-scoped unless we have *positive*
    evidence that the thread is public. A failed/unavailable authoritative
    lookup, a missing type, or an unrecognized type therefore all resolve to
    "private" (scope to members) rather than "broadcast to the whole server".
    A payload that explicitly says "public"/"announcement" is still safe to
    route normally when no authoritative value is available.
    """
    authoritative = None
    # Tested as ``thread_id is not None`` rather than through a saved bool:
    # that is the form a type checker can narrow, and it keeps the fail-closed
    # default for a missing thread id.
    lookup_failed = True
    if thread_id is not None:
        lookup_failed = False
        try:
            from src.core import threads as _threads

            if _threads.is_setup():
                authoritative = _threads.get_thread_type_value(thread_id)
            else:
                lookup_failed = True
        except Exception:
            lookup_failed = True

    if authoritative is not None:
        # Authoritative DB value wins over any caller-supplied claim. Anything
        # that is not a *known public* type (incl. unknown values) is scoped.
        return not _is_known_public_thread_type(authoritative)

    # No authoritative row: trust an explicit public claim, otherwise fail
    # closed (this covers the THREAD_DELETE fail-open on a failed tombstone
    # lookup and any payload that omits/mangles thread_type).
    if lookup_failed and not _is_known_public_thread_type(claimed_type):
        return True
    if _is_private_thread_type(claimed_type):
        return True
    if _is_known_public_thread_type(claimed_type):
        return False
    return True


def _private_thread_recipients(thread_id: Optional[int], is_private: bool):
    """Resolve the exact audience for a private-thread gateway event.

    Returns an explicit recipient list for private threads so the event
    router never expands the event to "all members of the server" (which
    would leak a private thread's name/owner/existence to non-members), and
    ``None`` for public/unknown threads so the caller keeps normal routing.

    Fail closed: when the threads module is unavailable or member lookup
    fails, return an empty list so the event reaches nobody rather than
    falling back to the unsafe server-wide audience.
    """
    if not is_private:
        return None
    if thread_id is None:
        return []
    try:
        from src.core import threads as _threads

        if not _threads.is_setup():
            return []
        return list(_threads.get_thread_member_user_ids(thread_id))
    except Exception:
        return []


def _thread_audience(
    thread_id: Optional[int], claimed_type: Any
) -> Optional[List[int]]:
    """Explicit member ids for a private-thread event; None for public routing."""
    if not _thread_privacy(thread_id, claimed_type):
        return None
    return _private_thread_recipients(thread_id, True)


def _known_private_thread_members(channel_id: Optional[int]) -> Optional[List[int]]:
    """Members if ``channel_id`` is an explicitly private thread, else None.

    Unlike :func:`_thread_audience` this does NOT fail closed merely because
    the type is unknown: it only scopes when the authoritative lookup says
    "private". Used for channel-scoped events (typing, read receipts, message
    events) where the channel is usually a normal channel/thread, so treating
    every unrecognized id as private would wrongly silence public traffic.
    """
    if channel_id is None:
        return None
    try:
        from src.core import threads as _threads

        if not _threads.is_setup():
            return None
        thread_type = _threads.get_thread_type_value(channel_id)
    except Exception:
        return None
    if _is_private_thread_type(thread_type):
        # Known private thread: member-scoped and fail closed on lookup error.
        return _private_thread_recipients(channel_id, True)
    return None


def _dm_participants_without_blocks(participants, actor_id: Optional[int]) -> List[int]:
    """Drop DM participants who have a block relationship with the actor.

    :meth:`EventRouter._get_dm_participant_ids` excludes either side of a block
    from a conversation's audience. Channel-scoped emitters bypass that router
    by handing the resolved audience to ``dispatch`` as an explicit
    ``user_ids`` list, so the block filter has to be applied here as well —
    without it a blocked user still received the actor's typing, read-receipt
    and reaction events even though the same event routed through the
    :class:`EventRouter` was withheld from them.

    Mirrors the router's semantics for a known actor: a participant that
    blocked the actor, and any participant the actor blocked, are excluded.
    When no relationship module is wired the existing audience is kept (the
    same fallback the router uses), so this never silences DM traffic. When a
    module IS wired but its authoritative block lookup fails, the audience is
    dropped entirely (fail closed) rather than delivered to a possibly blocked
    pair.
    """
    if actor_id is None:
        return list(participants)
    try:
        import src.api as api

        relationships = api.get_relationships()
    except Exception:
        relationships = None
    if relationships is None:
        return list(participants)

    def _as_id(value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None

    actor = _as_id(actor_id)
    if actor is None:
        return list(participants)

    participant_ids: List[int] = []
    for value in participants:
        # SECURITY: this loop builds the POSITIVE audience. participants comes
        # from raw DB projections (msg_participants.user_id / srv_members.user_id /
        # private-thread member rows) with no type filtering. The tolerant
        # _as_id() coercion below is deliberately reserved for block-set
        # COMPARISON keys, where accepting a string-stored legacy block id is
        # required for blocking to keep working. Using it here would launder
        # "05"->5, 7.9->7 and " 8 "->8 into principals that never proved
        # participation -- and it runs BEFORE _final(), so _final() strict guard
        # cannot catch it. Require a genuine int (type() also rejects bool).
        if type(value) is not int:
            continue
        if value not in participant_ids:
            participant_ids.append(value)

    excluded: set = set()

    # Resolve every participant's two-way block set in ONE batched call. The
    # old loop issued a cached lookup per participant on this gateway hot path
    # (typing, read receipts, reactions): an N+1 fan-out on cold caches and a
    # burst of Valkey GETs even when warm. The relationship manager's bounded,
    # chunked helper returns the very same sets; fall back to per-id lookups
    # only for lightweight stubs that do not implement it.
    bulk_lookup = getattr(relationships, "get_all_blocked_ids_bulk", None)
    if callable(bulk_lookup):
        try:
            bulk = cast(Mapping[Any, Any], bulk_lookup(participant_ids))
            blocked_by_id = {int(uid): values for uid, values in bulk.items()}
        except Exception as e:
            logger.debug(f"bulk blocked-id lookup failed for DM audience: {e}")
            # Fail closed: without authoritative sets no participant can be
            # proven safe to receive this actor's events.
            return []
    else:
        blocked_by_id = {}
        # Include the actor: their own block set drives the "users I blocked"
        # exclusion below. Skipping them here would silently drop that rule.
        for pid in participant_ids:
            try:
                blocked_by_id[pid] = relationships.get_all_blocked_ids(pid) or []
            except Exception as e:
                logger.debug(f"blocked-id lookup failed for {pid}: {e}")
                # Same fail-closed rule on the compatibility path.
                return []

    for pid in participant_ids:
        if pid == actor:
            continue
        # A participant that blocked the actor must not see the actor's events.
        if any(_as_id(bid) == actor for bid in blocked_by_id.get(pid) or []):
            excluded.add(pid)

    # Users the actor blocked must not see the actor's events either. The bulk
    # response already contains the actor's own two-way set.
    for value in blocked_by_id.get(actor) or []:
        bid = _as_id(value)
        if bid is not None:
            excluded.add(bid)

    return [pid for pid in participant_ids if pid not in excluded]


def _coerce_scope_id(value: Any) -> Optional[int]:
    """Normalize a scope id for the cache key without accepting booleans."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _rbac_epoch(server_id: int) -> Optional[int]:
    """Current RBAC revision for ``server_id``, or ``None`` on any failure.

    Every role / membership / override / ownership mutation advances the
    channel-access cache's server epoch (see ``ChannelAccessCache.bump``). A
    recipient list is a ``channels.view`` permission verdict, so folding this
    epoch into the cache key makes a stale audience unreachable the instant a
    viewer loses access instead of lingering for the whole TTL. ``None`` means
    the version store could not be read: callers must then skip the memo so an
    unverifiable audience is never replayed.
    """
    try:
        from src.core.servers.channel_access_cache import get_channel_access_cache

        return int(get_channel_access_cache().version(int(server_id), 0)[0])
    except Exception:
        return None


def resolve_channel_event_recipients(
    channel_id: Optional[int],
    *,
    guild_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    exclude_user_ids: Optional[List[int]] = None,
) -> List[int]:
    """Resolve the exact audience of a channel-scoped gateway event.

    Routing an event by ``server_id`` lets the event router expand it to
    *every* member of the server, which leaks typing indicators, read
    receipts and message activity in restricted/private channels. This helper
    returns an explicit recipient list instead, so callers pass it as
    ``user_ids`` and the router cannot widen the audience:

      * a channel that is a known private thread -> its thread members only;
        a failed member lookup yields an empty list (fail closed);
      * a server channel -> members that can actually VIEW it (``channels.view``);
        without a permission API the result is empty (fail closed);
      * otherwise -> DM / group conversation participants.

    The actor is always excluded (their client already applied the action).

    The result is memoized in a bounded, short-TTL cache keyed by the *complete*
    resolution identity: channel, guild, actor, exclusions, the identity of the
    audience-source modules and the threads module, and the server's RBAC epoch.
    Every input that can change the audience is in the key -- including the
    epoch that a permission revoke bumps -- so a stale ``channels.view`` verdict
    is unreachable on the very next emit and a different scope or a re-setup of
    the audience sources can never reuse another audience's recipients. Intent
    filtering remains at dispatch time and is unaffected.
    """
    if channel_id is None:
        return []

    exclude = set()
    for uid in exclude_user_ids or []:
        coerced = _coerce_scope_id(uid)
        if coerced is not None:
            exclude.add(coerced)
    actor = _coerce_scope_id(actor_id)
    if actor is not None:
        exclude.add(actor)

    # Fetch the audience sources once: the cheap lookups are also folded into
    # the cache key (by identity) so swapping a source guarantees a fresh
    # resolution rather than replaying a stale audience.
    try:
        import src.api as api

        servers = api.get_servers()
        messaging = api.get_messaging()
        relationships = api.get_relationships()
    except Exception:
        servers = None
        messaging = None
        relationships = None

    # The server scope drives the RBAC epoch that versions this audience. Use
    # the caller's guild hint when present (free); callers without one (polls,
    # some read paths) need one channel lookup to discover it, which the
    # server-channel branch below reuses so the hot path stays at one lookup.
    server_scope = _coerce_scope_id(guild_id)
    channel = None
    if server_scope is None and servers is not None:
        try:
            channel = servers.get_channel(channel_id, actor_id)
        except Exception:
            channel = None
        if channel is not None:
            server_scope = _coerce_scope_id(getattr(channel, "server_id", None))

    rbac_epoch = _rbac_epoch(server_scope) if server_scope is not None else None
    # A server-scoped audience whose epoch cannot be read must not reuse a warm
    # list (fail closed for freshness). Unscoped DM resolution has no
    # permission-filtered membership, so it keeps using the memo.
    use_cache = server_scope is None or rbac_epoch is not None

    cache_parts = (
        _coerce_scope_id(channel_id),
        _coerce_scope_id(guild_id),
        actor,
        tuple(sorted(exclude)),
        id(servers),
        id(messaging),
        id(relationships),
        id(sys.modules.get("src.core.threads")),
        rbac_epoch,
    )
    cached = _channel_recipient_cache.get(cache_parts) if use_cache else None
    if cached is not None:
        return cached

    def _final(ids) -> List[int]:
        """Dedupe genuine ints (preserving order), drop excluded ids, cache."""
        seen = set()
        result = []
        for uid in ids:
            # SECURITY: _final is the common audience boundary for the raw
            # srv_members.user_id and msg_participants.user_id projections
            # (no type filtering at source). int() laundering of a malformed-
            # but-coercible value ("05"->5, 7.9->7, " 8 "->8, True->1) would
            # admit a principal that never proved view/participation rights,
            # then persist it in the recipient cache. type() also rejects bool;
            # genuine integer ids are preserved verbatim.
            if type(uid) is not int:
                continue
            if uid not in exclude and uid not in seen:
                seen.add(uid)
                result.append(uid)
        if use_cache:
            _channel_recipient_cache.put(cache_parts, result)
        return result

    # 1. Known private thread: exact member set (fail closed on lookup error).
    thread_members = _known_private_thread_members(channel_id)
    if thread_members is not None:
        # A private thread is an intimate audience, just like a DM. The
        # channel-scoped emitters (typing, read receipts, reactions, polls)
        # pass this list to ``dispatch`` as an explicit ``user_ids`` audience,
        # so the EventRouter's DM block filter is bypassed for these events.
        # Apply the same two-way filter here: a thread member who blocked the
        # actor, or whom the actor blocked, must not receive the actor's
        # ephemeral events. Without it a blocked member still saw them even
        # though the identical event routed through EventRouter was withheld.
        return _final(_dm_participants_without_blocks(thread_members, actor_id))

    # 2. Server channel: members with channels.view, never the raw member list.
    server_id = server_scope
    if channel is None and servers is not None:
        try:
            channel = servers.get_channel(channel_id, actor_id)
        except Exception:
            channel = None
        if channel is not None:
            server_id = (
                _coerce_scope_id(getattr(channel, "server_id", None)) or server_id
            )
        elif guild_id is not None and actor_id is not None:
            # FAIL CLOSED: the authoritative lookup says the actor cannot see
            # this channel (unknown, deleted, non-member or no channels.view).
            # A caller-supplied ``guild_id`` is only a routing hint, not proof
            # of access; using it here would reconstruct a server-wide audience
            # for a channel the actor cannot view and leak the event to members
            # of the claimed server. DM callers pass no guild_id and still fall
            # through to participant resolution below.
            return _final([])

    if server_id is not None and servers is not None:
        has_permission = getattr(servers, "has_permission", None)
        if not callable(has_permission):
            # Cannot verify channel access -> no recipients (fail closed).
            return _final([])
        try:
            member_ids = servers.get_member_user_ids(int(server_id)) or []
        except Exception:
            member_ids = []
        recipients = []
        for uid in member_ids:
            try:
                if has_permission(uid, int(server_id), "channels.view", channel_id):
                    recipients.append(uid)
            except Exception:
                continue
        # The viewer walk above is permission-only: a blocked pair can share a
        # server and both retain `channels.view`. These recipients are handed to
        # `dispatch` as an explicit `user_ids` list, so the EventRouter's DM
        # block gate never sees them. Apply the same two-way gate used by the
        # DM/private-thread branches here too: a viewer who blocked the actor,
        # or whom the actor blocked, must not receive the actor's ephemeral
        # channel event. The helper fails closed when the block set cannot be
        # resolved. (REST typing/read receipts enforce this at their own layer.)
        return _final(_dm_participants_without_blocks(recipients, actor_id))

    # 3. DM / group conversation participants.
    if messaging is not None:
        try:
            manager = (
                messaging.get_manager()
                if hasattr(messaging, "get_manager")
                else messaging
            )
            participants = manager.get_participant_ids(channel_id)
            return _final(_dm_participants_without_blocks(participants or [], actor_id))
        except Exception:
            pass
    return _final([])


# --------------------------------------------------------------------------
# User / profile
# --------------------------------------------------------------------------
@_non_raising
def emit_user_update(
    user: Dict[str, Any], exclude_user_id: Optional[int] = None
) -> None:
    """Notify connected clients that a user's profile changed (avatar, username...)."""
    uid = user.get("id")
    _emit(
        EventType.USER_UPDATE,
        {"user": stringify_snowflake_ids(user)},
        user_ids=[int(uid)] if uid is not None else None,
        exclude_user_ids=[exclude_user_id] if exclude_user_id is not None else None,
    )


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
@_non_raising
def emit_message_delete_bulk(
    channel_id: int,
    message_ids: List[int],
    guild_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> None:
    """Notify clients that a batch of messages was deleted from a channel.

    The scope key is canonical ``server_id`` (matching single
    MESSAGE_DELETE); ``guild_id`` stays as a legacy alias. Recipients are
    resolved to the channel's actual viewers (thread members for a private
    thread) so a bulk delete in a restricted channel cannot leak its message
    ids to every server member. The legacy routing keys are kept for intent
    filtering, but the explicit ``user_ids`` take precedence in the router.
    """
    sid = str(guild_id) if guild_id is not None else None
    recipients = resolve_channel_event_recipients(
        channel_id, guild_id=guild_id, actor_id=actor_id
    )
    _emit(
        EventType.MESSAGE_DELETE_BULK,
        {
            "channel_id": str(channel_id),
            "message_ids": [str(mid) for mid in message_ids],
            "server_id": sid,
            "guild_id": sid,
        },
        user_ids=recipients,
        channel_id=channel_id,
        server_id=guild_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


@_non_raising
def emit_message_reaction_remove_all(
    channel_id: int,
    message_id: int,
    guild_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> None:
    """Notify clients that all reactions on a message were cleared.

    Payload id fields are stringified (and the acting user included) so the
    shape matches MESSAGE_REACTION_ADD/REMOVE events. Recipients are the
    channel's actual viewers, never the whole server member list.
    """
    recipients = resolve_channel_event_recipients(
        channel_id, guild_id=guild_id, actor_id=actor_id
    )
    _emit(
        EventType.MESSAGE_REACTION_REMOVE_ALL,
        {
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "guild_id": str(guild_id) if guild_id is not None else None,
            "user_id": str(actor_id) if actor_id is not None else None,
        },
        user_ids=recipients,
        channel_id=channel_id,
        server_id=guild_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


# --------------------------------------------------------------------------
# Polls
# --------------------------------------------------------------------------
@_non_raising
def emit_poll_update(
    poll_id: int,
    message_id: int,
    action: str,
    actor_id: Optional[int] = None,
    is_ended: Optional[bool] = None,
    channel_id: Optional[int] = None,
) -> None:
    """Notify channel viewers that a poll changed (vote/change/remove/close).

    The payload carries identifiers only — no vote counts — because
    result visibility is per-viewer; clients refetch
    ``GET /polls/{id}/results`` with their own credentials when the
    event arrives. Recipients are restricted to the channel's actual viewers.
    """
    recipients = resolve_channel_event_recipients(channel_id, actor_id=actor_id)
    _emit(
        EventType.POLL_UPDATE,
        {
            "poll_id": str(poll_id),
            "message_id": str(message_id),
            "channel_id": str(channel_id) if channel_id is not None else None,
            "action": action,
            "actor_id": str(actor_id) if actor_id is not None else None,
            "is_ended": is_ended,
        },
        user_ids=recipients,
        channel_id=channel_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


@_non_raising
def emit_message_reaction_remove_emoji(
    channel_id: int,
    message_id: int,
    emoji: str,
    guild_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> None:
    """Notify clients that every reaction of one emoji was removed by a moderator.

    Scoped to the channel's actual viewers so a moderation action in a
    private/restricted channel is not announced to the entire server.
    """
    recipients = resolve_channel_event_recipients(
        channel_id, guild_id=guild_id, actor_id=actor_id
    )
    _emit(
        EventType.MESSAGE_REACTION_REMOVE_EMOJI,
        {
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "emoji": emoji,
            "guild_id": str(guild_id) if guild_id is not None else None,
            "user_id": str(actor_id) if actor_id is not None else None,
        },
        user_ids=recipients,
        channel_id=channel_id,
        server_id=guild_id,
        exclude_user_ids=[actor_id] if actor_id is not None else None,
    )


@_non_raising
def emit_channel_pins_update(
    channel_id: int,
    guild_id: Optional[int] = None,
    pinned_message_ids: Optional[List[int]] = None,
    actor_id: Optional[int] = None,
) -> None:
    """Notify clients that the pinned-message set of a channel changed.

    Payload id fields are stringified so the shape matches the other
    message-family events; ``server_id`` is canonical with ``guild_id``
    kept as a legacy alias (matching MESSAGE_DELETE_BULK). Recipients are
    the channel's actual viewers, so a pin in a private/restricted channel is
    not announced to every member of the server.

    ``actor_id`` is the user who pinned/unpinned. It MUST be threaded into
    ``resolve_channel_event_recipients``: that resolver hands an explicit
    ``user_ids`` audience to ``dispatch``, bypassing the EventRouter's DM
    block gate. Without the actor the two-way block filter is skipped and a
    viewer who blocked the actor (or whom the actor blocked) still receives
    the actor's channel event -- the same leak already closed for typing,
    read receipts and reactions. The resolver fails closed when the block
    set cannot be resolved.
    """
    sid = str(guild_id) if guild_id is not None else None
    recipients = resolve_channel_event_recipients(
        channel_id, guild_id=guild_id, actor_id=actor_id
    )
    _emit(
        EventType.CHANNEL_PINS_UPDATE,
        {
            "channel_id": str(channel_id),
            "server_id": sid,
            "guild_id": sid,
            "pinned_message_ids": [str(mid) for mid in (pinned_message_ids or [])],
        },
        user_ids=recipients,
        channel_id=channel_id,
        server_id=guild_id,
    )


# --------------------------------------------------------------------------
# Servers / guilds
# --------------------------------------------------------------------------
@_non_raising
def emit_guild_update(
    server_id: int,
    *,
    name: Optional[str] = None,
    owner_id: Optional[int] = None,
    icon: Optional[str] = None,
) -> None:
    """Notify connected clients that a server's details changed (icon, name...).

    ``create_guild_update`` existed but was never dispatched anywhere, so
    server icon/name changes were silently invisible to connected clients
    until they re-fetched (stale icons in every open session).
    """
    data: Dict[str, Any] = {"id": str(server_id)}
    # ``is not None`` (not truthiness) so callers can explicitly clear a
    # field (e.g. ``icon=""`` when a server icon is deleted).
    if name is not None:
        data["name"] = name
    if owner_id is not None:
        data["owner_id"] = str(owner_id)
    if icon is not None:
        data["icon"] = icon
    _emit(EventType.GUILD_UPDATE, data, server_id=server_id)


# --------------------------------------------------------------------------
# Server roles
# --------------------------------------------------------------------------
@_non_raising
def emit_guild_role_create(server_id: int, role: Dict[str, Any]) -> None:
    _emit(
        EventType.GUILD_ROLE_CREATE,
        {"guild_id": str(server_id), "role": stringify_snowflake_ids(role)},
        server_id=server_id,
    )


@_non_raising
def emit_guild_role_update(server_id: int, role: Dict[str, Any]) -> None:
    _emit(
        EventType.GUILD_ROLE_UPDATE,
        {"guild_id": str(server_id), "role": stringify_snowflake_ids(role)},
        server_id=server_id,
    )


@_non_raising
def emit_guild_role_delete(server_id: int, role_id: int) -> None:
    _emit(
        EventType.GUILD_ROLE_DELETE,
        {"guild_id": str(server_id), "role_id": str(role_id)},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Server bans
# --------------------------------------------------------------------------
@_non_raising
def emit_guild_ban_add(
    server_id: int, user_id: int, reason: Optional[str] = None
) -> None:
    _emit(
        EventType.GUILD_BAN_ADD,
        {"guild_id": str(server_id), "user_id": str(user_id), "reason": reason},
        server_id=server_id,
    )


@_non_raising
def emit_guild_ban_remove(server_id: int, user_id: int) -> None:
    _emit(
        EventType.GUILD_BAN_REMOVE,
        {"guild_id": str(server_id), "user_id": str(user_id)},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Invites
# --------------------------------------------------------------------------
@_non_raising
def emit_invite_create(server_id: int, invite: Dict[str, Any]) -> None:
    _emit(
        EventType.INVITE_CREATE,
        {"guild_id": str(server_id), "invite": stringify_snowflake_ids(invite)},
        server_id=server_id,
    )


@_non_raising
def emit_invite_delete(server_id: int, code: str) -> None:
    _emit(
        EventType.INVITE_DELETE,
        {"guild_id": str(server_id), "code": code},
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------
@_non_raising
def emit_webhooks_update(server_id: int, channel_id: Optional[int] = None) -> None:
    _emit(
        EventType.WEBHOOKS_UPDATE,
        {
            "guild_id": str(server_id),
            "channel_id": str(channel_id) if channel_id is not None else None,
        },
        server_id=server_id,
    )


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------
@_non_raising
def emit_thread_create(thread: Dict[str, Any]) -> None:
    # A private thread's payload (name, owner, existence) must never be
    # broadcast to every member of the server. Scope it to the thread's
    # exact member set; public threads keep the normal server routing.
    tid = _coerce_routing_id(thread.get("id"))
    recipients = _thread_audience(tid, thread.get("thread_type"))
    if recipients is not None:
        _emit(
            EventType.THREAD_CREATE,
            stringify_snowflake_ids(thread),
            user_ids=recipients,
        )
        return
    _emit(
        EventType.THREAD_CREATE,
        stringify_snowflake_ids(thread),
        channel_id=_coerce_routing_id(thread.get("parent_id")),
        server_id=_coerce_routing_id(thread.get("guild_id")),
    )


@_non_raising
def emit_thread_update(thread: Dict[str, Any]) -> None:
    tid = _coerce_routing_id(thread.get("id"))
    recipients = _thread_audience(tid, thread.get("thread_type"))
    if recipients is not None:
        _emit(
            EventType.THREAD_UPDATE,
            stringify_snowflake_ids(thread),
            user_ids=recipients,
        )
        return
    _emit(
        EventType.THREAD_UPDATE,
        stringify_snowflake_ids(thread),
        channel_id=_coerce_routing_id(thread.get("parent_id")),
        server_id=_coerce_routing_id(thread.get("guild_id")),
    )


@_non_raising
def emit_thread_delete(
    thread_id: int,
    guild_id: Optional[int] = None,
    parent_id: Optional[int] = None,
    thread_type: Optional[Any] = None,
) -> None:
    # Privacy is resolved from the explicit argument first, then from the
    # authoritative threads module (which sees the tombstone). If neither can
    # positively establish "public", _thread_audience fails closed and scopes
    # to members instead of broadcasting a private thread's name/existence to
    # the entire server.
    payload = {
        "id": str(thread_id),
        "guild_id": str(guild_id) if guild_id is not None else None,
        "parent_id": str(parent_id) if parent_id is not None else None,
    }
    recipients = _thread_audience(_coerce_routing_id(thread_id), thread_type)
    if recipients is not None:
        _emit(EventType.THREAD_DELETE, payload, user_ids=recipients)
        return
    _emit(
        EventType.THREAD_DELETE,
        payload,
        channel_id=parent_id,
        server_id=guild_id,
    )


# --------------------------------------------------------------------------
# PlexiSum workbooks (realtime collaboration)
# --------------------------------------------------------------------------
@_non_raising
def emit_workbook_update(
    workbook_id: int,
    revision: int,
    actor_id: int,
    data: Dict[str, Any],
    user_ids: Optional[List[int]] = None,
    exclude_user_ids: Optional[List[int]] = None,
) -> None:
    """Notify everyone with workbook access that a mutation landed.

    The actor is typically excluded (they applied the change locally); every
    other viewer refreshes their viewport when the event arrives. ``data``
    carries extras (e.g. mention targets for comments); the workbook id,
    new revision and actor id are always (re)stated from the explicit
    arguments so a caller cannot emit an identifier-less event. Id fields
    are stringified like the message family; ``revision`` stays numeric.
    """
    payload = dict(data or {})
    payload["workbook_id"] = str(workbook_id)
    payload["revision"] = int(revision)
    payload["actor_id"] = str(actor_id)
    _emit(
        EventType.WORKBOOK_UPDATE,
        # ``data`` carries opaque extras (comment bodies, mention targets);
        # stringify id-shaped keys so a nested snowflake cannot leak as a
        # JSON number. ``revision`` and other non-id numerics pass through.
        stringify_snowflake_ids(payload),
        user_ids=user_ids,
        exclude_user_ids=exclude_user_ids,
    )


# --------------------------------------------------------------------------
# Security / account alerts (bridges the audit log to live clients)
# --------------------------------------------------------------------------
@_non_raising
def emit_security_alert(
    user_id: int,
    action: str,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Push a real-time security/account alert to a specific user's sessions.

    This is the live counterpart of the audit log. Examples: a session was
    revoked, 2FA was disabled, the password changed, or the account was
    locked. The client renders these via the SECURITY_ALERT dispatch event.
    """
    _emit(
        EventType.SECURITY_ALERT,
        {
            "user_id": str(user_id),
            "action": action,
            "detail": stringify_snowflake_ids(detail or {}),
        },
        user_ids=[user_id],
    )


# --------------------------------------------------------------------------
# Channel-scoped ephemeral events (typing, read receipts)
# --------------------------------------------------------------------------
# These used to be dispatched with a ``server_id`` (or with the unfiltered
# ``get_member_user_ids`` list), which the router expands to *every* server
# member. For a restricted or private channel that leaks who is typing and
# who read which message. The emitters below resolve the actual member set via
# ``resolve_channel_event_recipients`` and always pass it as explicit
# ``user_ids`` so the router cannot widen the audience.
@_non_raising
def emit_typing_start(
    channel_id: int,
    user_id: int,
    username: Optional[str] = None,
    *,
    guild_id: Optional[int] = None,
) -> None:
    """Notify only the channel's viewers that a user started typing."""
    recipients = resolve_channel_event_recipients(
        channel_id, guild_id=guild_id, actor_id=user_id
    )
    _emit(
        EventType.TYPING_START,
        {
            "channel_id": str(channel_id),
            "user_id": str(user_id),
            "username": username or "Someone",
        },
        user_ids=recipients,
        # Carry the real scope: without it both the local intent gate and the
        # cross-worker envelope classify this DM-capable event as guild-scoped.
        server_id=guild_id,
        channel_id=channel_id,
    )


@_non_raising
def emit_typing_stop(
    channel_id: int,
    user_id: int,
    *,
    guild_id: Optional[int] = None,
) -> None:
    """Notify only the channel's viewers that a user stopped typing."""
    recipients = resolve_channel_event_recipients(
        channel_id, guild_id=guild_id, actor_id=user_id
    )
    _emit(
        EventType.TYPING_STOP,
        {"channel_id": str(channel_id), "user_id": str(user_id)},
        user_ids=recipients,
        server_id=guild_id,
        channel_id=channel_id,
    )


@_non_raising
def emit_message_ack(
    channel_id: int,
    user_id: int,
    message_id: Optional[int] = None,
    *,
    guild_id: Optional[int] = None,
) -> None:
    """Broadcast a read receipt to the channel's actual viewers only.

    The sender is excluded (their client already rendered the checkmark).
    """
    recipients = resolve_channel_event_recipients(
        channel_id, guild_id=guild_id, actor_id=user_id
    )
    _emit(
        EventType.MESSAGE_ACK,
        {
            "channel_id": str(channel_id),
            "user_id": str(user_id),
            "message_id": str(message_id) if message_id is not None else None,
        },
        user_ids=recipients,
        server_id=guild_id,
        channel_id=channel_id,
    )
