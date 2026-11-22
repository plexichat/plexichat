from typing import Optional, List, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SnowflakeID, SnowflakeIDParam
from src.core.database.cache.decorators import cached
from src.core.events.snowflakes import stringify_snowflake_ids
from src.core.profiles.manager import PROFILE_READ_CACHE_PREFIX, ProfileManager
from src.api.routes.users.helpers import authenticated_cache_key, require_user_token
from src.core.security.hardening import (
    HardeningViolation,
    reject_control_characters,
    require_http_url,
)
from .common import raise_bad_request
import utils.logger as logger

router = APIRouter()

# H1 FIX: profile privacy enforcement. The profile owner controls who may read
# the quasi-identifying fields (location / timezone / pronouns / social links)
# through the client-synced ``profile_visibility`` KV setting; the server is
# authoritative for enforcement at read time.
PROFILE_VISIBILITY_SETTING_KEY = "profile_visibility"

# Client-facing vocabulary (settings UI stores these verbatim):
#   everyone -> public, friends -> friends, servers -> shared-server
#   membership, none -> private. Legacy server-side values (public/private)
#   are accepted for backward compatibility.
_VISIBILITY_ALIASES = {
    "everyone": "public",
    "public": "public",
    "friends": "friends",
    "servers": "shared-servers",
    "none": "private",
    "private": "private",
}
# Unset/empty setting: no explicit owner choice yet, so the historical
# documented default (public) applies.
_DEFAULT_VISIBILITY = "public"
# Sentinel for a *stored but unrecognized* value: fail closed to the limited
# projection instead of leaking the full profile.
_FAIL_CLOSED_VISIBILITY = "limited"

# Fields considered safe for a limited (non-friend / unknown-value) view.
_LIMITED_PROFILE_FIELDS = (
    "id",
    "user_id",
    "bio",
    "banner_url",
    "custom_status_text",
    "custom_status_emoji",
    "custom_status_expires_at",
    "created_at",
    "updated_at",
)

# Minimal public identity returned when visibility is "none"/"private".
_PRIVATE_PROFILE_FIELDS = ("user_id", "created_at")


# --- Bounded, per-credential cache for the profile metadata read ------------
#
# ``GET /users/{user_id}/profile`` resolves a ``user_profiles`` row (plus social
# link JSON and status fields) on every view. Multiple viewers polling the same
# profile re-run that query each time. Memoize the *raw* fetch for a short
# window. Keys are partitioned per credential via ``authenticated_cache_key`` so
# a bot token can never replay its owner's -- or another viewer's -- entry, and
# a bounded process-local L1 tier keeps warm reads off the shared backend.
#
# The visibility/authz decision is deliberately NOT cached: it is recomputed on
# every request (``_profile_projection_for``) *before* the cache is consulted,
# so a revoked friend/server relationship or a visibility change is reflected
# immediately and no view is ever served from a warm entry without re-checking
# who is asking. ``skip_cache_if`` is defence in depth: only user tokens
# populate the cache even if a caller reaches the loader directly.
_PROFILE_READ_TTL_SECONDS = 60
_PROFILE_READ_L1_TTL_SECONDS = 5


MAX_SOCIAL_LINKS = 10
MAX_SOCIAL_LINK_FIELD = 200
MAX_STATUS_EMOJI_CHARS = 8


def _validate_emoji(value: str) -> str:
    """A status emoji is a short, control-free unicode token, not HTML."""
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_STATUS_EMOJI_CHARS:
        raise ValueError("Invalid status emoji")
    if any(char in "<>&\"'\\" for char in candidate):
        raise ValueError("Invalid status emoji")
    try:
        reject_control_characters(candidate, "status_emoji")
    except HardeningViolation as exc:
        raise ValueError(str(exc)) from exc
    return candidate


def _validate_social_links(value) -> Optional[List[dict]]:
    """Bound the social-link array and validate every embedded string."""
    if value is None:
        return value
    if not isinstance(value, list) or len(value) > MAX_SOCIAL_LINKS:
        raise ValueError("Too many social links")
    checked: List[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("Invalid social link")
        clean: dict = {}
        for key, raw in entry.items():
            if not isinstance(key, str) or len(key) > MAX_SOCIAL_LINK_FIELD:
                raise ValueError("Invalid social link key")
            if raw is None:
                clean[key] = None
                continue
            if not isinstance(raw, str):
                raise ValueError("Invalid social link value")
            if len(raw) > MAX_SOCIAL_LINK_FIELD:
                raise ValueError("Social link value is too long")
            try:
                reject_control_characters(raw, key)
            except HardeningViolation as exc:
                raise ValueError(str(exc)) from exc
            if key in ("url", "href", "link") and raw:
                try:
                    clean[key] = require_http_url(raw, key)
                except HardeningViolation as exc:
                    raise ValueError(str(exc)) from exc
            else:
                clean[key] = raw
        checked.append(clean)
    return checked


class ProfileUpdateRequest(BaseModel):
    bio: Optional[str] = Field(None, max_length=1000, description="Profile bio")
    status: Optional[str] = Field(
        None, max_length=128, description="Custom status text"
    )
    status_emoji: Optional[str] = Field(None, max_length=32, description="Status emoji")
    pronouns: Optional[str] = Field(None, max_length=40, description="Pronouns")
    location: Optional[str] = Field(None, max_length=100, description="Location")
    timezone: Optional[str] = Field(None, max_length=64, description="Timezone")
    banner_url: Optional[str] = Field(None, description="Banner image URL")
    social_links: Optional[List[dict]] = Field(None, description="Social link objects")

    @field_validator("bio", "status", "pronouns", "location", "timezone")
    @classmethod
    def _safe_profile_text(cls, value, info):
        if value is None:
            return value
        try:
            return reject_control_characters(value, info.field_name)
        except HardeningViolation as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("status_emoji")
    @classmethod
    def _safe_emoji(cls, value, info):
        return None if value is None else _validate_emoji(value)

    @field_validator("social_links")
    @classmethod
    def _safe_links(cls, value, info):
        return _validate_social_links(value)


# --- OpenAPI documentation models for the projected response variants -----
# These document the three GET projections (full / limited / minimal); the
# endpoint keeps returning plain dicts so the wire format stays unchanged.


class FullProfile(BaseModel):
    """Complete profile (self, or viewer within the owner's visibility scope)."""

    id: Optional[SnowflakeID] = None
    user_id: SnowflakeID
    bio: Optional[str] = None
    banner_url: Optional[str] = None
    social_links: List[dict] = []
    custom_status_text: Optional[str] = None
    custom_status_emoji: Optional[str] = None
    custom_status_expires_at: Optional[int] = None
    pronouns: Optional[str] = None
    location: Optional[str] = None
    timezone: Optional[str] = None
    created_at: int
    updated_at: int


class LimitedProfile(BaseModel):
    """Limited projection (non-friend viewers under friends-only, shared-server
    denial, or any unrecognized visibility value — fail closed)."""

    id: Optional[SnowflakeID] = None
    user_id: SnowflakeID
    bio: Optional[str] = None
    banner_url: Optional[str] = None
    custom_status_text: Optional[str] = None
    custom_status_emoji: Optional[str] = None
    custom_status_expires_at: Optional[int] = None
    created_at: int
    updated_at: int


class PrivateProfile(BaseModel):
    """Minimal identity (visibility ``none``)."""

    user_id: SnowflakeID
    created_at: int


class ProfileResponse(BaseModel):
    profile: Union[FullProfile, LimitedProfile, PrivateProfile]


def invalidate_profile_visibility_cache(owner_id: int) -> None:
    """Drop the short-TTL cached raw visibility value for ``owner_id``.

    Called by the settings write paths when ``profile_visibility`` changes so
    enforcement follows writes immediately; the TTL bounds staleness anyway.
    """
    try:
        from src.core.database.cache.operations import invalidate_cached

        invalidate_cached(f"profile_visibility:int:{int(owner_id)}")
    except Exception as e:
        logger.debug(f"Profile visibility cache invalidation failed: {e}")


@cached(ttl=30, prefix="profile_visibility")
def _read_profile_visibility_raw(owner_id: int) -> Optional[str]:
    """Short-TTL cached read of the owner's raw profile-visibility setting."""
    settings_module = api.get_settings()
    if not settings_module:
        return None
    try:
        return settings_module.get_setting(owner_id, PROFILE_VISIBILITY_SETTING_KEY)
    except Exception as e:
        logger.debug(f"Failed to read profile_visibility for {owner_id}: {e}")
        return None


def _get_profile_visibility(owner_id: int) -> str:
    """Resolve and normalize the owner's stored profile-visibility setting.

    Client vocabulary (everyone/friends/servers/none) is mapped onto the
    internal enforcement levels; legacy values (public/private) still work.
    A missing value falls back to the documented default (public); an
    UNRECOGNIZED stored value fails closed to the limited projection.
    """
    # ``@cached`` derives its key from the runtime type name of the argument.
    # The HTTP route passes ``owner_id`` as a ``SnowflakeIDParam`` (an ``int``
    # subclass), which would produce ``profile_visibility:SnowflakeIDParam:<id>``
    # while the settings write path invalidates ``profile_visibility:int:<id>``.
    # Normalize to a plain ``int`` here so the read and invalidation keys agree:
    # otherwise a tightened visibility (public -> private/friends/servers) stays
    # warm for the full TTL and keeps exposing private fields to viewers.
    # A profile_visibility row lives in the database independently of the
    # settings module being wired in this process. When that module is unwired
    # _read_profile_visibility_raw cannot read the row and returns None, which
    # below would fall back to the public default and expose a profile the
    # owner explicitly stored as private/friends/servers. Fail closed to the
    # restricted projection instead; a genuinely absent row (settings wired)
    # still gets the documented public default.
    if not api.get_settings():
        return _FAIL_CLOSED_VISIBILITY
    raw = _read_profile_visibility_raw(int(owner_id))
    if not raw:
        return _DEFAULT_VISIBILITY
    value = str(raw).strip().lower().strip('"')
    return _VISIBILITY_ALIASES.get(value, _FAIL_CLOSED_VISIBILITY)


def _is_friend_of(viewer_id: int, owner_id: int) -> bool:
    relationships = api.get_relationships()
    if not relationships:
        return False
    try:
        return viewer_id in relationships.get_friend_ids(owner_id)
    except Exception as e:
        logger.debug(f"Friend lookup failed for {owner_id}: {e}")
        return False


def _shares_server_with(viewer_id: int, owner_id: int) -> bool:
    """Whether viewer and owner share at least one server membership."""
    servers = api.get_servers()
    if servers:
        try:
            return owner_id in servers.get_all_shared_member_ids(viewer_id)
        except Exception as e:
            logger.debug(f"Shared-server lookup failed for {viewer_id}: {e}")
            return False
    # Fallback when no servers module is wired (minimal deployments).
    relationships = api.get_relationships()
    if not relationships:
        return False
    try:
        return bool(relationships.get_mutual_servers(viewer_id, owner_id))
    except Exception as e:
        logger.debug(f"Mutual-server lookup failed for {viewer_id}: {e}")
        return False


def _is_blocked_between(viewer_id: int, owner_id: int) -> bool:
    """Whether a block relationship exists in either direction, resolved live.

    A block is a hard deny for cross-user profile reads *regardless* of the
    owner's ``profile_visibility``. ``_profile_projection_for`` consults this on
    every request, so a just-blocked viewer loses access immediately even while
    the raw profile metadata is still warm in ``user_profile_read_api`` -- a
    cached "allowed" projection can never outlive the relationship change. The
    verdict is deliberately not cached here: an unblock must take effect on the
    very next read too.
    """
    relationships = api.get_relationships()
    if not relationships:
        return False
    checker = getattr(relationships, "is_blocked_by_either", None)
    if callable(checker):
        try:
            return bool(checker(viewer_id, owner_id))
        except Exception as e:
            # Fail closed: if the authoritative block lookup is unavailable the
            # viewer must not be granted a full profile by default.
            logger.debug(f"Block lookup failed for {viewer_id}/{owner_id}: {e}")
            return True
    # Minimal/alternate relationship objects may only expose the block sets.
    lookup = getattr(relationships, "get_all_blocked_ids", None)
    if callable(lookup):
        try:
            return int(owner_id) in {int(uid) for uid in lookup(viewer_id)}
        except Exception as e:
            logger.debug(f"Block-set lookup failed for {viewer_id}: {e}")
            return True
    return False


@cached(
    ttl=_PROFILE_READ_TTL_SECONDS,
    prefix=PROFILE_READ_CACHE_PREFIX,
    key_builder=authenticated_cache_key(PROFILE_READ_CACHE_PREFIX),
    l1_ttl=_PROFILE_READ_L1_TTL_SECONDS,
    skip_cache_if=lambda current_user, **kwargs: (
        getattr(current_user, "token_type", "user") != "user"
    ),
)
def _load_profile_metadata(*, user_id: int, current_user) -> dict:
    """Cold path: fetch raw profile metadata from the database.

    Authorization is resolved by ``_profile_projection_for`` on *every*
    request before this cached function is entered (see the route below), so a
    cached raw row can never bypass an authz decision -- only the authoritative
    fetch is memoized, projection is always applied freshly. The credential is
    part of the key because bot tokens share their owner's ``user_id``.
    """
    db = api.get_db()
    return ProfileManager(db).get_profile(user_id) or {}


def _profile_projection_for(current_user, owner_id: int):
    """Resolve the visibility decision for this viewer *before* any cache hit.

    Returns ``None`` for a full projection (self-read, or public/allowed scope)
    or the tuple of field names to project for a restricted view. Keeping this
    decision outside the cached loader means a denied/restricted view is never
    stored and a warm entry can't serve stale authorization.
    """
    # A bot token's ``user_id`` is its human owner (the bot id lives in
    # ``account_id``). Treating that as the viewer here would let a leaked bot
    # token exercise the owner's friendships / shared-server memberships to
    # read another user's restricted profile -- the same bot-token-as-owner
    # confusion rejected on the owner-scoped routes. Bot credentials are
    # downgraded to an anonymous viewer: public profiles stay readable, but no
    # relationship-gated (or self) projection is granted.
    viewer_id = (
        current_user.user_id
        if getattr(current_user, "token_type", None) == "user"
        else None
    )
    if viewer_id is not None and owner_id == viewer_id:
        return None  # self-reads are always complete
    # A block is a hard deny independent of the owner's visibility setting.
    # This decision is recomputed on every request (never cached), so blocking
    # a viewer immediately revokes their profile access even though the raw
    # row is still warm in the per-credential profile read cache. Without this
    # check a blocked viewer kept reading the full profile through a warm
    # "allowed" entry until the TTL lapsed.
    if viewer_id is not None and _is_blocked_between(viewer_id, owner_id):
        return _PRIVATE_PROFILE_FIELDS
    visibility = _get_profile_visibility(owner_id)
    if visibility == "private":
        return _PRIVATE_PROFILE_FIELDS
    if visibility in ("friends", "shared-servers", _FAIL_CLOSED_VISIBILITY):
        allowed = False
        if viewer_id is not None:
            if visibility == "friends":
                allowed = _is_friend_of(viewer_id, owner_id)
            elif visibility == "shared-servers":
                allowed = _shares_server_with(viewer_id, owner_id)
        if not allowed:
            return _LIMITED_PROFILE_FIELDS
    return None


def _project_profile(profile: dict, fields) -> dict:
    projected = {key: profile.get(key) for key in fields}
    # The default/empty shape always exposes an empty social_links list so the
    # response contract stays stable across visibility levels.
    if "social_links" in fields:
        projected["social_links"] = []
    return projected


def _profile_payload(profile: dict) -> dict:
    """Wrap a profile dict, emitting its snowflake ids as decimal strings.

    The profile's ``id`` (profile row) and ``user_id`` are snowflakes, and a
    JS ``Number`` cannot hold a 64-bit id exactly, so serving them as JSON
    numbers would let the client round them.
    """
    return {"profile": stringify_snowflake_ids(profile)}


# Known validation failures raised by the profile manager, mapped to static
# client-safe messages (the raw ValueError text is internal detail).
_PROFILE_VALIDATION_MESSAGES = (
    ("social links allowed", "Too many social links"),
    ("banner url", "Invalid banner URL"),
    ("expiry time", "Invalid custom status expiry time"),
)


def _safe_validation_message(e: ValueError) -> str:
    text = str(e).lower()
    for needle, message in _PROFILE_VALIDATION_MESSAGES:
        if needle in text:
            return message
    return "Invalid profile data"


@router.get(
    "/users/{user_id}/profile",
    summary="Get user profile",
    responses={
        200: {
            "model": ProfileResponse,
            "description": (
                "Projected per the owner's profile_visibility: full profile "
                "(everyone/public, friend under friends-only, shared-server "
                "member under servers), limited fields (friends/servers scope "
                "denied, or unrecognized stored value), or minimal identity "
                "(none/private). Self-reads are always full."
            ),
        },
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_user_profile(
    user_id: SnowflakeIDParam, current_user: TokenInfo = Depends(get_current_user)
) -> dict:
    uid = user_id

    # AUTHZ GATE -- runs on every request, before the cache lookup. The
    # projection decision is derived live from the viewer credential and the
    # owner's current visibility, so a revoked relationship, a visibility
    # change, or a bot credential can never be answered from a warm entry.
    fields = _profile_projection_for(current_user, uid)

    # Only the authoritative (and expensive) metadata fetch is cached, under a
    # per-credential key with a bounded L1 tier. A hit still goes through the
    # fresh projection decision above before anything is returned.
    result = _load_profile_metadata(user_id=uid, current_user=current_user)
    if not result:
        return _profile_payload({"user_id": uid, "bio": None, "social_links": []})

    if fields is None:
        return _profile_payload(result)
    return _profile_payload(_project_profile(result, fields))


@router.patch(
    "/users/@me/profile",
    summary="Update your profile",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def update_own_profile(
    body: ProfileUpdateRequest, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
    db = api.get_db()

    svc = ProfileManager(db)

    try:
        result = svc.update_profile(
            user_id=current_user.user_id,
            bio=body.bio,
            pronouns=body.pronouns,
            location=body.location,
            timezone=body.timezone,
            banner_url=body.banner_url,
            social_links=body.social_links,
        )

        # Forward custom status / emoji to the profile custom-status store,
        # preserving any sibling fields the client did not mention (the manager
        # treats unspecified fields as "leave unchanged").
        extra: dict = {}
        if "status" in body.model_fields_set:
            extra["text"] = body.status
        if "status_emoji" in body.model_fields_set:
            extra["emoji"] = body.status_emoji
        if extra:
            svc.set_custom_status(user_id=current_user.user_id, **extra)
            result = svc.get_profile(current_user.user_id)

        return {"success": True, "profile": stringify_snowflake_ids(result)}
    except ValueError as exc:
        logger.debug("Profile validation failed for %s", current_user.user_id)
        raise_bad_request(_safe_validation_message(exc))
