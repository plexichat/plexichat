import asyncio
import time
from collections import OrderedDict
from threading import RLock
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, Depends, Request
from starlette.concurrency import run_in_threadpool

from src.core.database import check_rate_limit
from src.core.auth.ip_canonical import canonicalize_ip_address
from src.utils.net import get_client_ip

import src.api as api
import src.core.events as events_mod
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.channels import (
    ChannelInviteCreateRequest,
    ChannelInviteResponse,
    InviteInfoResponse,
    InviteJoinResponse,
)
from src.api.schemas.common import ErrorResponse, SnowflakeID

import utils.logger as logger

from .base import ChannelBase
from .helpers import (
    bounded_bulk_batch,
    require_channel_snowflake,
    require_invite_code,
    require_invite_limits,
    require_target_user_id,
)
from ..exception_mapping import raise_mapped, map_invite_join_error


# ``get_invite`` is a single indexed lookup on srv_invites.code, but resolving
# the invite's *display* server name used to call ``get_server`` on every GET:
# that runs a membership check plus a server row with three correlated count
# subqueries, all to read one string. Cache just that enrichment value (keyed
# by viewer so membership semantics cannot leak across users) for a short TTL.
# Live fields such as ``uses`` still come from ``get_invite`` on every request.
_INVITE_META_TTL_SECONDS = 60.0
_INVITE_META_MAXSIZE = 256


class _InviteMetadataCache:
    """Small, bounded, thread-safe TTL cache keyed by (user_id, server_id)."""

    def __init__(
        self, maxsize: int = _INVITE_META_MAXSIZE, ttl: float = _INVITE_META_TTL_SECONDS
    ):
        self._maxsize = max(1, int(maxsize))
        self._ttl = max(0.0, float(ttl))
        self._entries: "OrderedDict[Tuple[int, int], Tuple[float, str]]" = OrderedDict()
        self._lock = RLock()

    def get(self, key: Tuple[int, int]) -> Optional[str]:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: Tuple[int, int], value: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._entries[key] = (now + self._ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)

    def evict(self, key: Tuple[int, int]) -> None:
        """Drop one viewer's entry (used when its live gate stops passing)."""
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_INVITE_META_CACHE = _InviteMetadataCache()


def get_invite_metadata_cache() -> _InviteMetadataCache:
    """Expose the process-wide invite metadata cache (tests/ops)."""
    return _INVITE_META_CACHE


# --- Invite-code redemption guess budget ---------------------------------
# An invite code is a bearer credential. redeeming it is the credential-check
# half of the invite system, so online guessing must be bounded just like
# password / TOTP / share-link verification. Invite *creation* already has a
# per-user rate limit (``_enforce_invite_quota``); without the matching guard on
# redemption a client with a stolen session could scan the code space at the
# generic transport rate. These counters are spent through the SHARED, atomic
# rate-limit backend so the budget holds across ``--scale backend=N`` workers;
# ``check_rate_limit`` fails closed on a limiter fault.

#: Bounded, honest defaults. Operators may tune them under ``[invites]``:
#: ``redeem_guess_max`` / ``redeem_guess_window_seconds`` (per source) and
#: ``redeem_guess_global_max`` (whole instance; 0 disables the global floor).
#: The global ceiling is deliberately generous so normal join traffic is never
#: refused; it exists to cap a botnet that rotates source IPs past the per-IP
#: budget. The per-source budget is the authoritative guessing guard.
_REDEEM_GUESS_MAX = 10
_REDEEM_GUESS_WINDOW_SECONDS = 60
_REDEEM_GUESS_GLOBAL_MAX = 600


def _redeem_guess_config() -> dict:
    try:
        import utils.config as config

        return config.get("invites", {}) or {}
    except Exception:
        return {}


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _invite_redeem_guess_key(user_id, ip_address: Optional[str]) -> str:
    """Authoritative per-source guess key: canonical IP first, user second.

    The IP is resolved from the trusted-proxy-aware extractor and canonicalised
    so every textual spelling of one host (IPv4-mapped IPv6, zone ids,
    compressed forms) shares ONE bucket. The user id is a secondary component
    so two accounts from the same host still share the host-level floor. The
    guessed code is deliberately NOT part of the key: including it would give
    every candidate its own fresh budget (no protection) and write the secret
    into the rate-limit store. Unknown/missing IPs fall back to a single shared
    bucket rather than becoming an unlimited path.
    """
    canonical = canonicalize_ip_address(ip_address) or "unknown"
    return f"invites:redeem-guess:ip:{canonical}:user:{user_id}"


def _enforce_invite_redeem_guess_limit(user_id, request: Optional[Request]) -> None:
    """Spend one redemption guess; refuse uniformly once a budget is spent.

    Call this BEFORE ``use_invite`` on every attempt so wrong guesses count too.
    A denial is a static 429 that never carries code-state information: the
    limiter runs before any code lookup, so a throttled answer is identical
    whether the code was real-but-dead or never existed. Any limiter fault is
    treated as an exhausted budget (fail closed, never an unlimited path).
    """
    cfg = _redeem_guess_config()
    max_per_source = _positive_int(
        cfg.get("redeem_guess_max", _REDEEM_GUESS_MAX), _REDEEM_GUESS_MAX
    )
    window = _positive_int(
        cfg.get("redeem_guess_window_seconds", _REDEEM_GUESS_WINDOW_SECONDS),
        _REDEEM_GUESS_WINDOW_SECONDS,
    )
    global_max = _nonnegative_int(
        cfg.get("redeem_guess_global_max", _REDEEM_GUESS_GLOBAL_MAX),
        _REDEEM_GUESS_GLOBAL_MAX,
    )

    ip_address = None
    if request is not None:
        try:
            ip_address = get_client_ip(request)
        except Exception as exc:
            logger.debug(f"invite redeem guess IP resolution failed: {exc}")
            ip_address = None

    allowed = False
    try:
        allowed, _ = check_rate_limit(
            _invite_redeem_guess_key(user_id, ip_address), max_per_source, window
        )
        # A global floor bounds a distributed attacker rotating IPs/users so
        # the per-source budget cannot simply be multiplied by source count.
        if allowed and global_max > 0:
            allowed, _ = check_rate_limit(
                "invites:redeem-guess:global", global_max, window
            )
    except Exception as exc:
        # A store outage must not reopen the guessing corridor.
        logger.debug(f"invite redeem guess limiter failed: {exc}")
        allowed = False
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": "Too many invite attempts",
                }
            },
        )


def _viewer_is_server_member(servers_mod, server_id, user_id) -> Optional[bool]:
    """Cheap, live membership verdict for a warm invite-metadata entry.

    The cached display name is membership-gated data: ``get_server`` only
    returns it to members. Serving a warm entry without re-checking lets a
    kicked/banned member keep reading the name for the whole cache TTL. The
    same cheap, revocation-invalidated gate the server detail route uses
    (``get_member``) is reused here so the cache hit stays cheap. Return
    ``None`` when the probe is unavailable or fails: callers must fail closed
    rather than let a warm value stand in for an unknown verdict.
    """
    probe = getattr(servers_mod, "get_member", None)
    if probe is None:
        return None
    try:
        return probe(server_id, user_id) is not None
    except Exception as exc:
        logger.debug(f"invite viewer membership check failed: {exc}")
        return None


def _resolve_invite_server_name(servers_mod, server_id, user_id) -> Optional[str]:
    """Resolve an invite's server name with a bounded per-viewer cache.

    The membership check runs through ``servers_mod.get_server`` on a miss and
    through the cheap ``get_member`` gate on a hit, so a viewer whose access was
    revoked stops seeing the name immediately instead of for the rest of the
    TTL. Only a successful, still-authorized name is served; denials and
    failures are never memoized.
    """
    cache_key = None
    try:
        cache_key = (int(user_id), int(server_id))
    except (TypeError, ValueError):
        # Unusual id types: skip caching rather than changing the lookup.
        pass

    if cache_key is not None:
        cached = _INVITE_META_CACHE.get(cache_key)
        if cached is not None:
            is_member = _viewer_is_server_member(servers_mod, server_id, user_id)
            if is_member is True:
                return cached
            # Denied -> drop the warm entry and fail closed. Unknown (probe
            # missing/errored) -> evict and fall through to get_server, whose
            # own live membership check decides; never trust the stale value.
            _INVITE_META_CACHE.evict(cache_key)
            if is_member is False:
                return None

    try:
        server = servers_mod.get_server(server_id, user_id)
    except Exception as server_error:
        logger.debug(f"invite server name lookup failed: {server_error}")
        return None

    name = getattr(server, "name", None) if server else None
    if name and cache_key is not None:
        _INVITE_META_CACHE.put(cache_key, name)
    return name


class ChannelInvitesMixin(ChannelBase):
    def _register_routes(self) -> None:
        self.router.add_api_route(
            "/{channel_id}/invites",
            self._create_channel_invite,
            methods=["POST"],
            response_model=ChannelInviteResponse,
            summary="Create channel invite",
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Invalid channel ID or data",
                },
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Access denied"},
                404: {"model": ErrorResponse, "description": "Channel not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )
        self.router.add_api_route(
            "/invites/{invite_code}",
            self._get_invite_info,
            methods=["GET"],
            response_model=InviteInfoResponse,
            summary="Get invite info",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                404: {
                    "model": ErrorResponse,
                    "description": "Invite not found or expired",
                },
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )
        self.router.add_api_route(
            "/invites/{invite_code}",
            self._join_server_via_invite,
            methods=["POST"],
            response_model=InviteJoinResponse,
            summary="Join server via invite",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Banned from server"},
                404: {
                    "model": ErrorResponse,
                    "description": "Invite not found or expired",
                },
                409: {"model": ErrorResponse, "description": "Already a member"},
                429: {
                    "model": ErrorResponse,
                    "description": "Too many invite redemption guesses",
                },
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )
        self.router.add_api_route(
            "/invites/{invite_code}",
            self._delete_invite,
            methods=["DELETE"],
            summary="Delete invite",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Access denied"},
                404: {"model": ErrorResponse, "description": "Invite not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )

    async def _create_channel_invite(
        self,
        channel_id: str,
        body: ChannelInviteCreateRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> ChannelInviteResponse:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        try:
            try:
                cid = require_channel_snowflake(channel_id)
            except HTTPException:
                logger.warning(f"Invalid channel ID format: {channel_id}")
                raise
            max_age, max_uses = require_invite_limits(body.max_age, body.max_uses)

            try:
                # Dual-probe 404-vs-403: resolve the membership/permission-
                # aware channel FIRST. On the overwhelmingly common success
                # path this is the only channel lookup needed; the cheap
                # existence probe is consulted only when ``get_channel``
                # declines, to tell a gone channel (404) from a caller that
                # is blocked (403). Running ``channel_exists`` unconditionally
                # used to add a raw SELECT to every successful invite create.
                # Status codes and the validation order are unchanged.
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if not channel:
                    if not servers_mod.channel_exists(cid):
                        raise HTTPException(
                            status_code=404,
                            detail={
                                "error": {
                                    "code": 404,
                                    "message": "Channel not found",
                                }
                            },
                        )
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Channel access denied",
                            }
                        },
                    )
                try:
                    invite = servers_mod.create_invite(
                        user_id=current_user.user_id,
                        channel_id=cid,
                        max_age=max_age,
                        max_uses=max_uses,
                        temporary=body.temporary,
                    )
                except Exception as exc:
                    raise_mapped(
                        exc,
                        not_found_message="Channel not found",
                        permission_message="Channel access denied",
                        invalid_message="Invalid invite parameters",
                    )
                try:
                    from src.core.events.gateway_emit import emit_invite_create

                    emit_invite_create(
                        invite.server_id if hasattr(invite, "server_id") else 0,
                        {
                            "code": invite.code,
                            "server_id": getattr(invite, "server_id", None),
                            "channel_id": cid,
                            "inviter_id": current_user.user_id,
                        },
                    )
                except Exception as ge:
                    logger.debug(f"emit_invite_create failed: {ge}")
                return ChannelInviteResponse(
                    code=invite.code,
                    channel_id=SnowflakeID(cid),
                    server_id=SnowflakeID(invite.server_id)
                    if hasattr(invite, "server_id")
                    else None,
                    max_age=max_age,
                    max_uses=max_uses,
                    temporary=body.temporary,
                    uses=0,
                    created_at=getattr(invite, "created_at", None),
                )
            except HTTPException:
                # Re-raise HTTPException FIRST so the dual-probe's
                # 404/403 status codes survive the outer ``except
                # Exception`` block that follows. The outer handler
                # maps typed core exceptions (NotFound / Permission /
                # Invalid) to status codes, but HTTPException is not
                # one of those types -- so without this re-raise the
                # dual-probe's status code gets degraded to a generic
                # 500. SELFTEST FIX.
                raise
            except Exception as e:
                raise_mapped(
                    e,
                    not_found_message="Channel not found",
                    permission_message="Channel access denied",
                    invalid_message="Invalid invite parameters",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in create_channel_invite for {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Internal server error",
                    }
                },
            )

    async def _get_invite_info(
        self, invite_code: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> InviteInfoResponse:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        try:
            try:
                invite_code = require_invite_code(invite_code)
                invite = servers_mod.get_invite(invite_code)
                if not invite:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Invite not found or expired",
                            }
                        },
                    )

                # Expired invites must not leak metadata through the info
                # endpoint: core ``get_invite`` keeps expired rows so the
                # redeem path can report its 410, but this endpoint promises
                # 404 for "not found or expired". Fail closed using the same
                # epoch-ms clock the invite was minted with.
                expires_at = getattr(invite, "expires_at", None)
                if expires_at is not None and expires_at <= int(time.time() * 1000):
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Invite not found or expired",
                            }
                        },
                    )

                # An exhausted invite (uses reached max_uses) is just as
                # unusable as an expired one. Core keeps the row (revoked = 0)
                # so the join path can report its 410, but this endpoint must
                # not hand the server/channel/inviter metadata to anyone who
                # merely holds the code. Fail closed with the same static 404
                # as the expired case, keeping the two unusable states
                # indistinguishable to a non-member probing invite codes.
                max_uses = getattr(invite, "max_uses", 0) or 0
                uses = getattr(invite, "uses", 0) or 0
                if max_uses > 0 and uses >= max_uses:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Invite not found or expired",
                            }
                        },
                    )

                server_name = getattr(invite, "server_name", None)
                if not server_name and hasattr(invite, "server_id"):
                    server_name = _resolve_invite_server_name(
                        servers_mod, invite.server_id, current_user.user_id
                    )

                raw_inviter_id = getattr(invite, "inviter_id", None)
                safe_inviter_id = (
                    require_target_user_id(raw_inviter_id, "inviter_id")
                    if raw_inviter_id
                    else None
                )
                return InviteInfoResponse(
                    code=invite.code,
                    server_id=SnowflakeID(invite.server_id)
                    if hasattr(invite, "server_id")
                    else None,
                    server_name=server_name,
                    channel_id=SnowflakeID(invite.channel_id)
                    if hasattr(invite, "channel_id")
                    else None,
                    inviter_id=SnowflakeID(safe_inviter_id)
                    if safe_inviter_id
                    else None,
                    uses=getattr(invite, "uses", 0),
                    max_uses=getattr(invite, "max_uses", 0),
                    expires_at=getattr(invite, "expires_at", None),
                )
            except HTTPException:
                raise
            except Exception as e:
                # Typed mapping: NotFound/Expired family -> 404.
                raise_mapped(
                    e,
                    not_found_message="Invite not found or expired",
                    permission_message="Access denied",
                    invalid_message="Invite not found or expired",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in get_invite_info for {invite_code}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Internal server error",
                    }
                },
            )

    async def _join_server_via_invite(
        self,
        invite_code: str,
        current_user: TokenInfo = Depends(get_current_user),
        request: Request = None,  # type: ignore[assignment]
    ) -> InviteJoinResponse:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        try:
            try:
                invite_code = require_invite_code(invite_code)
                # Bound online guessing of this bearer credential BEFORE any
                # code lookup. Wrong guesses consume the same budget as real
                # attempts; the shared counter is keyed on the authoritative
                # client IP (canonicalised) and fails closed on limiter faults.
                _enforce_invite_redeem_guess_limit(current_user.user_id, request)
                result = servers_mod.use_invite(current_user.user_id, invite_code)

                sid = getattr(result, "server_id", None) or (
                    result if isinstance(result, (int, str)) else None
                )

                asyncio.create_task(
                    self._dispatch_invite_join_events(servers_mod, sid, current_user)
                )

                return InviteJoinResponse(
                    success=True,
                    server_id=SnowflakeID(sid) if sid else None,
                )
            except Exception as e:
                map_invite_join_error(e)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in join_server_via_invite for {invite_code}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Internal server error",
                    }
                },
            )

    async def _dispatch_invite_join_events(
        self, servers_mod, sid, current_user: TokenInfo
    ) -> None:
        """Fan out GUILD_CREATE / member-add events for a successful join.

        The four storage reads are independent, so they run in a single
        ``asyncio.gather`` instead of three concurrent reads followed by a
        sequential fourth. Each collection is capped exactly once. Kept as a
        named coroutine (rather than an inline closure) so the query fan-out
        is directly testable; failure handling and the ``ws_is_setup`` gate
        are unchanged.
        """
        try:
            from src.api.websocket import (
                get_dispatcher,
                is_setup as ws_is_setup,
            )

            if not ws_is_setup():
                return

            dispatcher = get_dispatcher()
            auth = api.get_auth()

            async def _fetch_sync(fn, *args, **kwargs):
                return await run_in_threadpool(fn, *args, **kwargs)

            async def _member_ids():
                fetch = getattr(servers_mod, "get_member_user_ids", None)
                if not fetch:
                    return []
                return await run_in_threadpool(
                    fetch, sid, exclude_user_id=current_user.user_id
                )

            results = await asyncio.gather(
                _fetch_sync(servers_mod.get_server, sid, current_user.user_id),
                _fetch_sync(servers_mod.get_channels, current_user.user_id, sid),
                _fetch_sync(servers_mod.get_roles, current_user.user_id, sid),
                _member_ids(),
                return_exceptions=True,
            )
            server, channels, roles, member_ids = results
            if isinstance(server, BaseException):
                server = None
            if isinstance(roles, BaseException):
                roles = []
            if isinstance(channels, BaseException):
                channels = []
            if isinstance(member_ids, BaseException):
                member_ids = []
            channels = bounded_bulk_batch(channels, "channels")
            roles = bounded_bulk_batch(roles, "roles")
            member_ids = bounded_bulk_batch(member_ids, "member_ids")

            if server:
                event = events_mod.create_guild_create(
                    server_id=int(sid or 0),
                    name=server.name,
                    owner_id=server.owner_id,
                    member_count=getattr(server, "member_count", 0),
                    channels=[
                        {
                            "id": str(c.id),
                            "name": c.name,
                            "type": getattr(c.channel_type, "value", c.channel_type)
                            if hasattr(c.channel_type, "value")
                            else c.channel_type,
                            "position": getattr(c, "position", 0),
                        }
                        for c in channels
                    ],
                    roles=[
                        {
                            "id": str(r.id),
                            "name": r.name,
                            "color": r.color,
                            "hoist": r.hoist,
                            "position": r.position,
                        }
                        for r in roles
                    ],
                )
                await dispatcher.dispatch_event(event, [current_user.user_id])

            user_data = None
            if auth:
                user = await run_in_threadpool(auth.get_user, current_user.user_id)
                if user:
                    user_data = {
                        "id": str(user.id),
                        "username": user.username,
                        "avatar_url": user.avatar_url,
                    }

            member_event = events_mod.create_guild_member_add(
                server_id=int(sid or 0),
                user_id=current_user.user_id,
                user=user_data,
            )
            if member_ids:
                await dispatcher.dispatch_event(member_event, member_ids)

        except Exception as de:
            logger.warning(f"Failed to dispatch join events for server {sid}: {de}")

    async def _delete_invite(
        self, invite_code: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> Dict[str, bool]:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        try:
            try:
                invite_code = require_invite_code(invite_code)
                # Capture the invite's server_id BEFORE deletion — after
                # delete_invite the row is gone (or revoked) and
                # get_invite returns None, which used to make us emit
                # INVITE_DELETE with guild_id=0 that every client drops.
                # F5: resolve the real guild id from the invite record;
                # fall back to 0 (with a warning) only when the invite
                # truly carries no server id.
                try:
                    inv = servers_mod.get_invite(invite_code)
                    server_id = getattr(inv, "server_id", None)
                except Exception:
                    server_id = None

                servers_mod.delete_invite(current_user.user_id, invite_code)
                try:
                    from src.core.events.gateway_emit import emit_invite_delete

                    if not server_id:
                        logger.warning(
                            f"INVITE_DELETE for {invite_code} has no server_id "
                            "on the invite record; emitting guild_id=0 fallback"
                        )
                        emit_invite_delete(0, invite_code)
                    else:
                        emit_invite_delete(int(server_id), invite_code)
                except Exception as ge:
                    logger.debug(f"emit_invite_delete failed: {ge}")
                return {"success": True}
            except Exception as e:
                raise_mapped(
                    e,
                    not_found_message="Invite not found",
                    permission_message="Access denied",
                    invalid_message="Invalid invite",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in delete_invite for {invite_code}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Internal server error",
                    }
                },
            )
