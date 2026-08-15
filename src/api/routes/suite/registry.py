"""Authenticated read-only PlexiSuite registry and universal-home routes.

This is the first runtime integration for the suite scaffold. It exposes the
server-authoritative app catalog and a bounded personal recent-artifacts feed.
Stash mutations and Sum workbook persistence remain separate domains and are not
silently routed through the generic artifact payload.
"""

import json
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, Query

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.routes.artifacts import _get_manager, _require_server_permission
from src.api.schemas.common import ErrorResponse
from src.core.artifacts.capabilities import (
    capability_allows_artifact,
    capability_to_dict,
    get_capability,
    is_usable,
)
from src.core.artifacts.models import Artifact
from src.core.suite.registry.schema import AppManifest
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


_SUITE_MANIFESTS = (
    AppManifest(
        app_id="plexichat",
        label="Plexichat",
        icon="ph-chat-circle",
        color_token="chat",
        scopes=["personal", "conversation", "server"],
        supports_embedded=False,
        supports_offline=True,
    ),
    AppManifest(
        app_id="plexiscribe",
        label="PlexiScribe",
        icon="ph-notebook",
        color_token="scribe",
        artifact_types=["plexiscribe"],
        capability="plexiscribe",
        scopes=["personal", "conversation", "server"],
        supports_embedded=True,
        supports_offline=True,
    ),
    AppManifest(
        app_id="plexiscript",
        label="PlexiScript",
        icon="ph-code",
        color_token="script",
        artifact_types=["plexiscript"],
        capability="plexiscript",
        scopes=["personal", "conversation", "server"],
        supports_embedded=True,
        supports_offline=True,
    ),
    AppManifest(
        app_id="plexiboard",
        label="PlexiBoard",
        icon="ph-chalkboard",
        color_token="board",
        artifact_types=["whiteboard"],
        capability="artifacts_whiteboard",
        scopes=["conversation", "server"],
        supports_embedded=True,
        supports_offline=False,
    ),
    AppManifest(
        app_id="plexistash",
        label="PlexiStash",
        icon="ph-vault",
        color_token="stash",
        artifact_types=["stash"],
        capability="plexistash",
        scopes=["personal", "server"],
        supports_embedded=True,
        supports_offline=True,
    ),
    AppManifest(
        app_id="plexisum",
        label="PlexiSum",
        icon="ph-table",
        color_token="sum",
        artifact_types=["sum"],
        capability="plexisum",
        scopes=["personal", "server"],
        supports_embedded=False,
        supports_offline=True,
    ),
)

_ARTIFACT_APP_IDS = {
    "plexiscribe": "plexiscribe",
    "plexiscript": "plexiscript",
    "whiteboard": "plexiboard",
}

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
    """Serialize a manifest without leaking implementation-only state."""
    capability = capabilities.get(manifest.capability) if manifest.capability else None
    result = {
        "id": manifest.app_id,
        "label": manifest.label,
        "icon": manifest.icon,
        "color": manifest.color_token,
        "artifactTypes": list(manifest.artifact_types),
        "capability": manifest.capability,
        "scopes": list(manifest.scopes),
        "supportsEmbedded": manifest.supports_embedded,
        "supportsOffline": manifest.supports_offline,
        "availability": "available",
    }
    if capability is not None:
        result["capabilityState"] = capability_to_dict(capability)
        result["availability"] = capability.state.value
    return result


def _artifact_home_item(artifact: Artifact) -> Dict[str, Any]:
    """Return the small, launcher-safe projection of an artifact."""
    return {
        "id": str(artifact.id),
        "appId": _ARTIFACT_APP_IDS.get(artifact.artifact_type.value, "plexichat"),
        "artifactType": artifact.artifact_type.value,
        "title": artifact.title,
        "summary": artifact.summary,
        "status": artifact.status.value,
        "scope": {
            "conversationId": str(artifact.conversation_id)
            if artifact.conversation_id is not None
            else None,
            "channelId": str(artifact.channel_id)
            if artifact.channel_id is not None
            else None,
            "serverId": str(artifact.server_id)
            if artifact.server_id is not None
            else None,
        },
        "createdAt": artifact.created_at,
        "updatedAt": artifact.updated_at,
    }


def _stash_home_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project Stash metadata without exposing storage references."""
    return {
        "id": str(item["id"]),
        "appId": "plexistash",
        "artifactType": "stash",
        "title": item.get("title") or "Untitled item",
        "summary": item.get("description"),
        "status": "active",
        "pinned": bool(item.get("pinned", False)),
        "scope": {
            "serverId": str(item["server_id"]) if item.get("server_id") else None
        },
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
    }


def _sum_home_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project workbook metadata into the shared launcher contract."""
    return {
        "id": str(item["id"]),
        "appId": "plexisum",
        "artifactType": "sum",
        "title": item.get("title") or "Untitled workbook",
        "summary": f"Revision {int(item.get('current_revision', 0))}",
        "status": "active",
        "scope": {
            "serverId": str(item["server_id"]) if item.get("server_id") else None
        },
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
    }


def _sort_and_bound(
    items: Iterable[Dict[str, Any]], limit: int
) -> List[Dict[str, Any]]:
    """Sort a bounded scatter/gather result without trusting client data."""
    return sorted(
        items,
        key=lambda item: int(item.get("updatedAt") or item.get("createdAt") or 0),
        reverse=True,
    )[:limit]


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
    rows = db.fetch_all(
        "SELECT server_id FROM srv_members WHERE user_id = ? LIMIT ?",
        (int(user_id), int(limit)),
    )
    return [int(row["server_id"]) for row in rows]


def _shared_artifacts(
    user_id: int,
    server_ids: List[int],
    limit: int,
    search: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Gather server artifacts only where the existing RBAC boundary permits view."""
    manager = _get_manager()
    if not server_ids:
        return []
    # Single ``server_id IN (...)`` query across the caller's memberships
    # instead of one query per membership. Fetch a bounded multiple so the
    # post-query authorization/capability filtering cannot starve the feed.
    fetch_limit = min(max(int(limit) * len(server_ids), int(limit)), 2000)
    filters: Dict[str, Any] = {
        "sort_by": "updated_at",
        "sort_order": "desc",
        "limit": fetch_limit,
    }
    if search:
        filters["search"] = search
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
    user_id: int, limit: int, search: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Gather server-scoped Stash, Sum, and authorized artifact items."""
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
    return _sort_and_bound(items, limit)


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
    # Evaluate only the capabilities the Suite manifests reference. The full
    # catalog includes voice transcription/recording, whose evaluators import
    # the transcription provider on every call — wasted work on this hot path.
    capabilities = {
        manifest.capability: get_capability(manifest.capability)
        for manifest in _SUITE_MANIFESTS
        if manifest.capability
    }
    return {
        "schemaVersion": 1,
        "apps": [
            _manifest_dict(manifest, capabilities) for manifest in _SUITE_MANIFESTS
        ],
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
    manager = _get_manager()
    filters = {
        "sort_by": "updated_at",
        "sort_order": "desc",
        "limit": int(limit),
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
        stash_items, _ = stash_manager.list_items(current_user.user_id, limit=limit)
        items.extend(_stash_home_item(item) for item in stash_items)
    if _domain_available("plexisum"):
        sum_items, _ = sum_manager.list_workbooks(
            current_user.user_id, limit=limit, offset=0
        )
        items.extend(_sum_home_item(item) for item in sum_items)
    bounded_items = _sort_and_bound(items, int(limit))
    return {
        "schemaVersion": 1,
        "items": bounded_items,
        "total": len(bounded_items),
        "hasMore": len(items) > len(bounded_items),
    }


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
    items = _shared_items(current_user.user_id, int(limit))
    return {"schemaVersion": 1, "items": items, "total": len(items), "hasMore": False}


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
        return ", ".join(parts)
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
    import src.api as api

    db = api.get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    rows = db.fetch_all(
        "SELECT id, domain, action, resource_type, resource_id, details_json, created_at "
        "FROM suite_audit_log WHERE actor_id = ? ORDER BY created_at DESC LIMIT ?",
        (int(current_user.user_id), int(limit)),
    )
    items = [
        {
            "id": str(row["id"]),
            "appId": "plexistash" if row["domain"] == "stash" else "plexisum",
            "artifactType": row["domain"],
            "title": f"{row['action']} {row['resource_type']}",
            "summary": _activity_summary(
                row.get("details_json"), row["action"], row["resource_type"]
            ),
            "status": "activity",
            "resourceId": str(row["resource_id"]) if row.get("resource_id") else None,
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
    query = q.strip()
    if not query:
        return {"schemaVersion": 1, "items": [], "total": 0, "hasMore": False}
    manager = _get_manager()
    artifacts = manager.list_with_filters(
        filters={
            "search": query,
            "sort_by": "updated_at",
            "sort_order": "desc",
            "limit": limit,
        },
        author_id=current_user.user_id,
    )
    items = [_artifact_home_item(artifact) for artifact in _suite_artifacts(artifacts)]
    stash_manager, sum_manager = _suite_managers()
    if _domain_available("plexistash"):
        stash_items, _ = stash_manager.list_items(
            current_user.user_id, limit=limit, search=query
        )
        items.extend(_stash_home_item(item) for item in stash_items)
    if _domain_available("plexisum"):
        # Server-side title search: matches workbooks beyond the first page,
        # unlike the previous bounded-list-then-filter approach.
        sum_items, _ = sum_manager.list_workbooks(
            current_user.user_id, limit=limit, offset=0, search=query
        )
        items.extend(_sum_home_item(item) for item in sum_items)
    # Universal search must also cover server-scoped (shared) items, matching
    # the scope of ``/suite/home/shared``, rather than personal scope only.
    items.extend(_shared_items(current_user.user_id, int(limit), search=query))
    bounded_items = _sort_and_bound(items, int(limit))
    return {
        "schemaVersion": 1,
        "items": bounded_items,
        "total": len(bounded_items),
        "hasMore": len(items) > len(bounded_items),
    }


__all__ = [
    "router",
    "list_suite_apps",
    "list_recent_suite_items",
    "list_pinned_suite_items",
    "list_shared_suite_items",
    "list_suite_activity",
    "search_suite",
]
