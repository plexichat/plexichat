"""Authenticated read-only PlexiSuite registry and universal-home routes.

This is the first runtime integration for the suite scaffold. It exposes the
server-authoritative app catalog and a bounded personal recent-artifacts feed.
Stash mutations and Sum workbook persistence remain separate domains and are not
silently routed through the generic artifact payload.
"""

import copy
import json
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import utils.logger as logger
from fastapi import APIRouter, Depends, Query

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.routes.users.helpers import require_user_token
from src.api.routes.artifacts import _get_manager, _require_server_permission
from src.api.schemas.common import ErrorResponse
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)
from src.core.artifacts.capabilities import (
    capability_allows_artifact,
    get_capability,
    is_usable,
)
from src.core.artifacts.models import Artifact
from src.core.suite.registry.schema import AppManifest, CapabilityProjection
from src.core.suite.registry.service import (
    SUITE_MANIFESTS,
    get_registry,
    manifest_to_dict,
    project_capability,
    scrub_details,
)
from src.core.suite.stash import StashManager
from src.core.suite.sum.manager import SumManager

router = APIRouter(
    prefix="/suite",
    tags=["PlexiSuite"],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
    },
)


# Canonical manifests live in the server-authoritative registry service;
# this alias is kept for backward compatibility with existing imports.
_SUITE_MANIFESTS = SUITE_MANIFESTS

_ARTIFACT_APP_IDS = {
    "plexiscribe": "plexiscribe",
    "plexiscript": "plexiscript",
    "whiteboard": "plexiboard",
}

#: The launcher routes an item to an app purely from ``appId``. An unexpected
#: value (a renamed artifact type, a bad DB row, or a future manifest typo)
#: must collapse to the safe chat fallback rather than become a dead card or a
#: route to an app the caller cannot open. Keep the catalog closed.
_VALID_APP_IDS = frozenset(
    {
        "plexichat",
        "plexistash",
        "plexisum",
        "plexiscribe",
        "plexiscript",
        "plexiboard",
    }
)


_MAX_SEARCH_LENGTH = 200
_MAX_SERVER_IDS = 100
_MAX_ACTIVITY_PART_LENGTH = 120


# The universal-home feeds are opened on every launcher start. Each miss fans
# out to the artifact repository plus the Stash/Sum listings (each with its own
# COUNT and visibility subqueries), so a burst of opens turns into repeated
# scatter/gather work. Memoize the fully projected response for a short TTL.
#
# The key ALWAYS includes the viewer id and every request input: one user's
# personalized feed can never be served to another (IDOR), and distinct
# queries cannot grow the cache without bound. Returned/stored values are deep
# copies so a caller mutating a response cannot poison a later cache hit.
_SUITE_FEED_CACHE_TTL_SECONDS = 2.0
_SUITE_FEED_CACHE_MAX_ENTRIES = 256
_suite_feed_cache: Dict[
    Tuple[int, str, int, Optional[str]], Tuple[float, Dict[str, Any]]
] = {}


def _clear_suite_feed_cache(user_id: Optional[int] = None) -> None:
    """Drop memoized suite feeds, optionally only one viewer's entries."""
    if user_id is None:
        _suite_feed_cache.clear()
        return
    uid = int(user_id)
    for key in [key for key in _suite_feed_cache if key[0] == uid]:
        _suite_feed_cache.pop(key, None)


def _suite_feed_from_cache(key: Tuple[int, str, int, Optional[str]]):
    """Return a memoized feed response, or ``None`` on miss/expiry."""
    cached = _suite_feed_cache.get(key)
    if (
        cached is not None
        and (time.monotonic() - cached[0]) < _SUITE_FEED_CACHE_TTL_SECONDS
    ):
        return copy.deepcopy(cached[1])
    return None


def _store_suite_feed(key: Tuple[int, str, int, Optional[str]], result: Dict[str, Any]):
    """Memoize a feed response with a hard size cap."""
    if len(_suite_feed_cache) >= _SUITE_FEED_CACHE_MAX_ENTRIES:
        _suite_feed_cache.clear()
    _suite_feed_cache[key] = (time.monotonic(), copy.deepcopy(result))


def clean_suite_search(value: Any) -> Optional[str]:
    """Bound and sanitize a suite search term before fan-out."""
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if len(candidate) > _MAX_SEARCH_LENGTH:
        candidate = candidate[:_MAX_SEARCH_LENGTH]
    reject_control_characters(candidate, "search")
    return candidate


def safe_suite_id(value: Any) -> Optional[str]:
    """Serialize a suite id only when it is a positive snowflake.

    Ids in the launcher payload are opaque strings, but storing an arbitrary
    object (or a locally rejected huge integer) as an id breaks dedupe keys and
    can be reflected into a client route. Validate once at the projection.
    """
    if value is None:
        return None
    try:
        return str(parse_id(value, "suite item ID"))
    except (HardeningViolation, TypeError, ValueError):
        return None


def safe_timestamp(value: Any) -> int:
    """Bound a timestamp used for ordering scatter/gather results."""
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if number < 0 or number > 4102444800000:
        return 0
    return number


def safe_app_id(value: Any, default: str = "plexichat") -> str:
    """Return ``value`` only when it names a known PlexiSuite app."""
    candidate = str(value or "").strip()
    if candidate in _VALID_APP_IDS:
        return candidate
    return default if default in _VALID_APP_IDS else "plexichat"


# Fail fast if a future edit adds an artifact mapping outside the catalog.
for _artifact_type, _mapped_app in _ARTIFACT_APP_IDS.items():
    if _mapped_app not in _VALID_APP_IDS:
        raise RuntimeError(f"Invalid suite app id for {_artifact_type}: {_mapped_app}")

# Only artifact types that map to a dedicated PlexiSuite app belong in the
# suite home feeds. Voice calls, uploads, files, and transcripts are chat
# artifacts surfaced by the chat artifacts pane, not suite work; surfacing
# them here (with a ``plexichat`` appId and a click that just closes back to
# chat) was misleading and produced dead cards.
_SUITE_ARTIFACT_TYPES = frozenset(_ARTIFACT_APP_IDS)


def _suite_artifacts(artifacts: Iterable[Artifact]) -> List[Artifact]:
    """Return only artifacts that map to a dedicated PlexiSuite app.

    Voice calls, uploads, files, and transcripts are chat artifacts surfaced by
    the chat artifacts pane. Surfacing them in the suite feeds (with a
    ``plexichat`` appId and a click that just closes back to chat) produced
    misleading dead cards, so they are excluded here.
    """
    return [
        artifact
        for artifact in artifacts
        if artifact.artifact_type.value in _SUITE_ARTIFACT_TYPES
    ]


def _manifest_dict(
    manifest: AppManifest, capabilities: Dict[str, Any]
) -> Dict[str, Any]:
    """Serialize a manifest without leaking implementation-only state.

    Backward-compatibility wrapper around the registry service serializer.
    ``capabilities`` maps capability names to scrubbed
    :class:`CapabilityProjection` objects. Raw ``CapabilityInfo`` values are
    still accepted defensively and scrubbed before serialization so older
    callers cannot reintroduce unsanitized ``details``.
    """
    from src.core.artifacts.capabilities import CapabilityInfo

    projection = capabilities.get(manifest.capability) if manifest.capability else None
    if isinstance(projection, CapabilityInfo):
        state = getattr(projection.state, "value", str(projection.state))
        projection = CapabilityProjection(
            feature=str(projection.feature),
            state=state,
            message=str(projection.message),
            details=scrub_details(projection.details),
        )
    elif isinstance(projection, CapabilityProjection):
        pass
    elif projection is None:
        pass
    else:
        # Unknown shape: fail closed rather than leak.
        projection = (
            project_capability(str(manifest.capability))
            if manifest.capability
            else None
        )
    return manifest_to_dict(manifest, projection)


def _artifact_home_item(artifact: Artifact) -> Dict[str, Any]:
    """Return the small, launcher-safe projection of an artifact."""
    return {
        "id": safe_suite_id(artifact.id),
        "appId": safe_app_id(
            _ARTIFACT_APP_IDS.get(artifact.artifact_type.value, "plexichat")
        ),
        "artifactType": artifact.artifact_type.value,
        "title": artifact.title,
        "summary": artifact.summary,
        "status": artifact.status.value,
        "scope": {
            "conversationId": safe_suite_id(artifact.conversation_id),
            "channelId": safe_suite_id(artifact.channel_id),
            "serverId": safe_suite_id(artifact.server_id),
        },
        "createdAt": artifact.created_at,
        "updatedAt": artifact.updated_at,
    }


def _stash_home_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project Stash metadata without exposing storage references."""
    return {
        "id": safe_suite_id(item["id"]),
        "appId": safe_app_id("plexistash"),
        "artifactType": "stash",
        "title": item.get("title") or "Untitled item",
        "summary": item.get("description"),
        "status": "active",
        "pinned": bool(item.get("pinned", False)),
        "scope": {"serverId": safe_suite_id(item["server_id"])},
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
    }


def _sum_home_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project workbook metadata into the shared launcher contract."""
    return {
        "id": safe_suite_id(item["id"]),
        "appId": safe_app_id("plexisum"),
        "artifactType": "sum",
        "title": item.get("title") or "Untitled workbook",
        "summary": f"Revision {safe_timestamp(item.get('current_revision', 0))}",
        "status": "active",
        "scope": {"serverId": safe_suite_id(item["server_id"])},
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
    }


def _sort_and_bound(
    items: Iterable[Dict[str, Any]], limit: int
) -> List[Dict[str, Any]]:
    """Sort a bounded scatter/gather result without trusting client data."""
    return sorted(
        items,
        key=lambda item: safe_timestamp(item.get("updatedAt") or item.get("createdAt")),
        reverse=True,
    )[:limit]


def _dedupe_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop repeat sightings of the same item, keeping first-seen order.

    Universal search fans out to a personal listing (all visible items,
    which already includes server-scoped rows) plus the shared/server
    listing, so a server-scoped Stash/Sum row or an author's own server
    artifact arrives twice. Key on the launcher identity (appId + id).
    """
    seen = set()
    unique: List[Dict[str, Any]] = []
    for item in items:
        key = (str(item.get("appId")), str(item.get("id")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _domain_available(capability: str) -> bool:
    """Fail closed when a licensed suite domain is unavailable or unlicensed."""
    return is_usable(get_capability(capability).state)


def _suite_managers() -> tuple[StashManager, SumManager]:
    """Return initialized managers, with a fixture-safe fallback."""
    import src.api as api
    from src.core.suite import stash as stash_domain
    from src.core.suite import sum as sum_domain

    db = api.get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    try:
        stash_manager = stash_domain.get_manager()
    except RuntimeError:
        stash_manager = StashManager(
            db, servers_module=api.get_servers(), media=api.get_media()
        )
    try:
        sum_manager = sum_domain.get_manager()
    except RuntimeError:
        sum_manager = SumManager(db, servers_module=api.get_servers())
    return stash_manager, sum_manager


def _member_server_ids(user_id: int, limit: int = 100) -> List[int]:
    """Return only servers the caller belongs to, bounded for scatter/gather."""
    import src.api as api

    db = api.get_db()
    if db is None:
        return []
    bounded = bounded_int(limit, "limit", minimum=1, maximum=_MAX_SERVER_IDS)
    rows = db.fetch_all(
        "SELECT server_id FROM srv_members WHERE user_id = ? LIMIT ?",
        (int(user_id), bounded),
    )
    ids: List[int] = []
    for row in rows:
        try:
            ids.append(parse_id(row["server_id"], "server ID"))
        except (HardeningViolation, TypeError, ValueError):
            continue
        if len(ids) >= _MAX_SERVER_IDS:
            break
    return ids


def _shared_artifacts(
    user_id: int,
    server_ids: List[int],
    limit: int,
    search: Optional[str] = Query(None, max_length=256),
) -> List[Dict[str, Any]]:
    """Gather server artifacts only where the existing RBAC boundary permits view."""
    manager = _get_manager()
    if not server_ids:
        return []
    # Single ``server_id IN (...)`` query across the caller's memberships
    # instead of one query per membership. Fetch a bounded multiple so the
    # post-query authorization/capability filtering cannot starve the feed.
    fetch_limit = min(
        max(bounded_int(limit, "limit", minimum=1, maximum=50) * len(server_ids), 1),
        2000,
    )
    filters: Dict[str, Any] = {
        "sort_by": "updated_at",
        "sort_order": "desc",
        "limit": fetch_limit,
    }
    if search:
        filters["search"] = clean_suite_search(search)
    artifacts = manager.list_with_filters(filters=filters, server_id=server_ids)

    # Authorize lazily: RBAC is checked only for servers that actually
    # produced a result (and memoized per request), instead of paying one
    # permission lookup per membership up front. Most memberships contribute
    # no suite artifacts, so this removes the dominant N+1 on the shared feed.
    permission_cache: Dict[int, bool] = {}

    def authorized(server_id: Optional[int]) -> bool:
        if server_id is None:
            return False
        sid = int(server_id)
        if sid not in permission_cache:
            permission_cache[sid] = _require_server_permission(
                user_id, sid, "artifact.view"
            )
        return permission_cache[sid]

    items = [
        _artifact_home_item(artifact)
        for artifact in _suite_artifacts(artifacts)
        if capability_allows_artifact(artifact.artifact_type)
        and authorized(artifact.server_id)
    ]
    return _sort_and_bound(items, int(limit))


def _shared_items(
    user_id: int, limit: int, search: Optional[str] = Query(None, max_length=256)
) -> List[Dict[str, Any]]:
    """Gather server-scoped Stash, Sum, and authorized artifact items.

    Returns the gathered matches newest-first without truncating to ``limit``.
    Callers bound to the page and report ``total``/``hasMore`` from the
    gathered count, matching the ``/suite/home/recent`` and ``/suite/search``
    contract. Truncating here would silently drop overflow and force callers
    to report ``hasMore=False`` alongside a truncated ``total``.
    """
    stash_manager, sum_manager = _suite_managers()
    server_ids = _member_server_ids(user_id)
    items: List[Dict[str, Any]] = []
    if _domain_available("plexistash"):
        stash_items, _ = stash_manager.list_items(
            user_id, limit=limit, scope="server", search=search
        )
        items.extend(_stash_home_item(item) for item in stash_items)
    if _domain_available("plexisum"):
        # Server-scoped only, filtered in SQL rather than fetched-then-discarded.
        sum_items, _ = sum_manager.list_workbooks(
            user_id, limit=limit, offset=0, scope="server", search=search
        )
        items.extend(_sum_home_item(item) for item in sum_items)
    items.extend(_shared_artifacts(user_id, server_ids, limit, search=search))
    return sorted(
        items,
        key=lambda item: safe_timestamp(item.get("updatedAt") or item.get("createdAt")),
        reverse=True,
    )


@router.get(
    "/apps",
    summary="List PlexiSuite apps and capabilities",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSuite is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_suite_apps(
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the capability-aware app catalog for the authenticated user."""
    del current_user  # Authentication is the visibility boundary for this catalog.
    registry = get_registry()
    manifests = registry.list_apps()
    # Evaluate only the capabilities the Suite manifests reference, through
    # the registry service so projections are scrubbed and fail closed. The
    # full catalog includes voice transcription/recording, whose evaluators
    # import the transcription provider on every call — wasted work here.
    capabilities = {
        manifest.capability: registry.get_capability(manifest.capability)
        for manifest in manifests
        if manifest.capability
    }
    return {
        "schemaVersion": 1,
        "apps": [_manifest_dict(manifest, capabilities) for manifest in manifests],
    }


@router.get(
    "/home/recent",
    summary="List recent personal PlexiSuite items",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSuite is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_recent_suite_items(
    limit: int = Query(24, ge=1, le=50),
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return only the caller's bounded recent artifacts.

    This endpoint deliberately uses the existing artifact repository and
    personal author scope. Shared/server recent activity will be added only with
    explicit scope authorization and pagination tests.
    """
    # A bot token's ``user_id`` is its human owner. The feed below keys both
    # its cache entry and its author-scoped lookups on that id, so without this
    # gate a bot credential would be served the owner's private recent items.
    require_user_token(current_user)
    bounded = bounded_int(limit, "limit", minimum=1, maximum=50)
    key = (int(current_user.user_id), "recent", bounded, None)
    cached = _suite_feed_from_cache(key)
    if cached is not None:
        return cached
    manager = _get_manager()
    filters = {
        "sort_by": "updated_at",
        "sort_order": "desc",
        "limit": bounded,
        "offset": 0,
        "author_id": current_user.user_id,
    }
    items: List[Dict[str, Any]] = [
        _artifact_home_item(artifact)
        for artifact in _suite_artifacts(
            manager.list_with_filters(filters=filters, author_id=current_user.user_id)
        )
    ]
    stash_manager, sum_manager = _suite_managers()
    if _domain_available("plexistash"):
        stash_items, _ = stash_manager.list_items(current_user.user_id, limit=bounded)
        items.extend(_stash_home_item(item) for item in stash_items)
    if _domain_available("plexisum"):
        sum_items, _ = sum_manager.list_workbooks(
            current_user.user_id, limit=bounded, offset=0
        )
        items.extend(_sum_home_item(item) for item in sum_items)
    bounded_items = _sort_and_bound(items, bounded)
    result = {
        "schemaVersion": 1,
        "items": bounded_items,
        "total": len(items),
        "hasMore": len(items) > len(bounded_items),
    }
    _store_suite_feed(key, result)
    return result


@router.get(
    "/home/pinned",
    summary="List pinned PlexiSuite items",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSuite is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_pinned_suite_items(
    limit: int = Query(24, ge=1, le=50),
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return pinned Stash items visible to the caller."""
    # A bot token's ``user_id`` is its human owner; reject it before the
    # owner-scoped ``list_items(current_user.user_id, ...)`` lookup below.
    require_user_token(current_user)
    if not _domain_available("plexistash"):
        return {"schemaVersion": 1, "items": [], "total": 0, "hasMore": False}
    stash_manager, _ = _suite_managers()
    items, total = stash_manager.list_items(
        current_user.user_id, limit=limit, pinned=True
    )
    bounded_items = [_stash_home_item(item) for item in items]
    return {
        "schemaVersion": 1,
        "items": bounded_items,
        "total": total,
        "hasMore": total > len(bounded_items),
    }


@router.get(
    "/home/shared",
    summary="List shared PlexiSuite items",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSuite is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_shared_suite_items(
    limit: int = Query(24, ge=1, le=50),
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return server-scoped items after each domain's authorization checks."""
    # A bot token's ``user_id`` is its human owner. The cache key and the
    # per-caller visibility lookups below are keyed on that id, so a bot must
    # not be able to reuse the owner's warm entry.
    require_user_token(current_user)
    bounded = bounded_int(limit, "limit", minimum=1, maximum=50)
    key = (int(current_user.user_id), "shared", bounded, None)
    cached = _suite_feed_from_cache(key)
    if cached is not None:
        return cached
    items = _shared_items(current_user.user_id, bounded)
    bounded_items = _sort_and_bound(items, bounded)
    result = {
        "schemaVersion": 1,
        "items": bounded_items,
        "total": len(items),
        "hasMore": len(items) > len(bounded_items),
    }
    _store_suite_feed(key, result)
    return result


# The activity feed reads a single actor's newest rows. Stash's canonical
# schema declares this covering index, but Suite routes can be reached before
# that schema has been applied (and on deployments where Stash is disabled).
# Ensure it once, idempotently, so the feed always has an index-backed access
# path. One attempt per process: on a read-only replica the DDL is skipped and
# retrying it on every request would only add overhead.
_ACTIVITY_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_suite_audit_actor "
    "ON suite_audit_log(actor_id, created_at DESC)"
)
_activity_index_ready = False


def _ensure_activity_index(db) -> None:
    """Best-effort, one-time creation of the feed's covering index."""
    global _activity_index_ready
    if _activity_index_ready:
        return
    try:
        db.execute(_ACTIVITY_INDEX_SQL)
    except Exception as e:  # pragma: no cover - depends on deployment role
        logger.debug(f"Suite activity index check skipped: {e}")
    _activity_index_ready = True


def _activity_summary(
    details_json: Any, action: str, resource_type: str
) -> Optional[str]:
    """Render audit details as a short, human-readable summary, never raw JSON."""
    if not details_json:
        return None
    try:
        details = json.loads(details_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(details, dict):
        return None
    parts: List[str] = []
    if details.get("pinned") is True:
        parts.append("pinned")
    if details.get("template") is True:
        parts.append("set as template")
    if "subject_id" in details:
        parts.append(f"with user {details['subject_id']}")
    permissions = details.get("permissions")
    if isinstance(permissions, list) and permissions:
        parts.append(", ".join(str(p) for p in permissions))
    if details.get("scope"):
        parts.append(str(details["scope"]))
    if details.get("item_type"):
        parts.append(str(details["item_type"]))
    if details.get("version_id") is not None:
        parts.append(f"version {details['version_id']}")
    if parts:
        return ", ".join(part[:_MAX_ACTIVITY_PART_LENGTH] for part in parts)
    return None


@router.get(
    "/home/activity",
    summary="List the caller's Suite activity",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSuite is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_suite_activity(
    limit: int = Query(24, ge=1, le=50),
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the caller's Suite audit activity, never another user's events."""
    # A bot token's ``user_id`` is its human owner; the SQL below filters on
    # that id, so a bot would otherwise read the owner's private audit trail.
    require_user_token(current_user)
    import src.api as api

    db = api.get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    _ensure_activity_index(db)
    rows = db.fetch_all(
        "SELECT id, domain, action, resource_type, resource_id, details_json, created_at "
        "FROM suite_audit_log WHERE actor_id = ? ORDER BY created_at DESC LIMIT ?",
        (int(current_user.user_id), int(limit)),
    )
    items = [
        {
            "id": safe_suite_id(row["id"]),
            "appId": safe_app_id(
                "plexistash" if row["domain"] == "stash" else "plexisum"
            ),
            "artifactType": row["domain"],
            "title": f"{row['action']} {row['resource_type']}",
            "summary": _activity_summary(
                row.get("details_json"), row["action"], row["resource_type"]
            ),
            "status": "activity",
            "resourceId": safe_suite_id(row.get("resource_id")),
            "createdAt": row["created_at"],
            "updatedAt": row["created_at"],
        }
        for row in rows
    ]
    return {"schemaVersion": 1, "items": items, "total": len(items), "hasMore": False}


@router.get(
    "/search",
    summary="Search visible PlexiSuite metadata",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSuite is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def search_suite(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(24, ge=1, le=50),
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Search bounded metadata through existing per-domain visibility rules."""
    # A bot token's ``user_id`` is its human owner. The search cache key and
    # the author-scoped lookups below are keyed on that id, so a bot credential
    # must not read or poison the owner's warm search results.
    require_user_token(current_user)
    query = clean_suite_search(q)
    if not query:
        return {"schemaVersion": 1, "items": [], "total": 0, "hasMore": False}
    bounded = bounded_int(limit, "limit", minimum=1, maximum=50)
    key = (int(current_user.user_id), "search", bounded, query)
    cached = _suite_feed_from_cache(key)
    if cached is not None:
        return cached
    manager = _get_manager()
    artifacts = manager.list_with_filters(
        filters={
            "search": query,
            "sort_by": "updated_at",
            "sort_order": "desc",
            "limit": bounded,
        },
        author_id=current_user.user_id,
    )
    items = [_artifact_home_item(artifact) for artifact in _suite_artifacts(artifacts)]
    stash_manager, sum_manager = _suite_managers()
    if _domain_available("plexistash"):
        stash_items, _ = stash_manager.list_items(
            current_user.user_id, limit=bounded, search=query
        )
        items.extend(_stash_home_item(item) for item in stash_items)
    if _domain_available("plexisum"):
        # Server-side title search: matches workbooks beyond the first page,
        # unlike the previous bounded-list-then-filter approach.
        sum_items, _ = sum_manager.list_workbooks(
            current_user.user_id, limit=bounded, offset=0, search=query
        )
        items.extend(_sum_home_item(item) for item in sum_items)
    # Universal search must also cover server-scoped (shared) items, matching
    # the scope of ``/suite/home/shared``, rather than personal scope only.
    # The personal listings above already include server-scoped rows visible
    # to the caller, so dedupe before bounding to avoid double cards.
    items.extend(_shared_items(current_user.user_id, bounded, search=query))
    items = _dedupe_items(items)
    bounded_items = _sort_and_bound(items, bounded)
    result = {
        "schemaVersion": 1,
        "items": bounded_items,
        "total": len(items),
        "hasMore": len(items) > len(bounded_items),
    }
    _store_suite_feed(key, result)
    return result


__all__ = [
    "router",
    "list_suite_apps",
    "list_recent_suite_items",
    "list_pinned_suite_items",
    "list_shared_suite_items",
    "list_suite_activity",
    "search_suite",
]
