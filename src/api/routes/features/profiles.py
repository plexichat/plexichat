from typing import Optional, List, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.core.database.cache.decorators import cached
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


# --- OpenAPI documentation models for the projected response variants -----
# These document the three GET projections (full / limited / minimal); the
# endpoint keeps returning plain dicts so the wire format stays unchanged.


class FullProfile(BaseModel):
    """Complete profile (self, or viewer within the owner's visibility scope)."""

    id: Optional[int] = None
    user_id: int
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

    id: Optional[int] = None
    user_id: int
    bio: Optional[str] = None
    banner_url: Optional[str] = None
    custom_status_text: Optional[str] = None
    custom_status_emoji: Optional[str] = None
    custom_status_expires_at: Optional[int] = None
    created_at: int
    updated_at: int


class PrivateProfile(BaseModel):
    """Minimal identity (visibility ``none``)."""

    user_id: int
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
    raw = _read_profile_visibility_raw(owner_id)
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


def _project_profile(profile: dict, fields) -> dict:
    projected = {key: profile.get(key) for key in fields}
    # The default/empty shape always exposes an empty social_links list so the
    # response contract stays stable across visibility levels.
    if "social_links" in fields:
        projected["social_links"] = []
    return projected


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
    "/users/{user_id:int}/profile",
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
    user_id: int, current_user: TokenInfo = Depends(get_current_user)
) -> dict:
    uid = user_id

    db = api.get_db()
    from src.core.profiles.manager import ProfileManager

    svc = ProfileManager(db)

    result = svc.get_profile(uid)
    if not result:
        return {"profile": {"user_id": uid, "bio": None, "social_links": []}}

    # Self-reads are always complete.
    if uid == current_user.user_id:
        return {"profile": result}

    visibility = _get_profile_visibility(uid)
    if visibility == "private":
        return {"profile": _project_profile(result, _PRIVATE_PROFILE_FIELDS)}
    if visibility in ("friends", "shared-servers", _FAIL_CLOSED_VISIBILITY):
        allowed = False
        if visibility == "friends":
            allowed = _is_friend_of(current_user.user_id, uid)
        elif visibility == "shared-servers":
            allowed = _shares_server_with(current_user.user_id, uid)
        if not allowed:
            return {"profile": _project_profile(result, _LIMITED_PROFILE_FIELDS)}
    return {"profile": result}


@router.patch(
    "/users/@me/profile",
    summary="Update your profile",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def update_own_profile(
    body: ProfileUpdateRequest, current_user: TokenInfo = Depends(get_current_user)
):
    db = api.get_db()
    from src.core.profiles.manager import ProfileManager

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

        return {"success": True, "profile": result}
    except ValueError as e:
        logger.debug(f"Profile validation failed for {current_user.user_id}: {e}")
        raise_bad_request(_safe_validation_message(e))
