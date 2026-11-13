"""Response serialization helpers for the PlexiStash routes."""

from typing import Any, Dict, List

import src.api as api

from src.api.schemas.stash import StashItemListResponse, StashItemResponse


def _item_response(item: Dict[str, Any]) -> StashItemResponse:
    """Serialize a single item, resolving embedded grantee identities."""
    if item.get("grants"):
        cache = _grant_profile_cache([g.get("subject_id") for g in item["grants"]])
        if cache:
            item = dict(item)
            item["grants"] = [_attach_grant_profile(g, cache) for g in item["grants"]]
    return StashItemResponse.model_validate(item)


def _grant_profile_cache(subject_ids: Any) -> Dict[str, Dict[str, Any]]:
    """Bulk-resolve grantee profiles for a batch of grants.

    Returns ``{str(subject_id): {"username": ..., "avatar_url": ...}}`` so list
    endpoints avoid an N+1 user lookup. Best-effort: on any failure an empty
    cache is returned and responses simply omit the identity fields (the
    client falls back to its own id-based rendering).
    """
    ids = sorted(
        {
            int(s)
            for s in (
                subject_ids
                if isinstance(subject_ids, (list, tuple, set))
                else [subject_ids]
            )
            if s is not None
        }
    )
    if not ids:
        return {}
    auth = api.get_auth()
    if auth is None:
        return {}
    try:
        profiles = auth.get_user_profiles_bulk(ids)
    except Exception as exc:
        import utils.logger as logger

        logger.debug(f"Failed to bulk-resolve stash grantee profiles: {exc}")
        return {}
    return {
        str(uid): {
            "username": profile.get("username"),
            "avatar_url": profile.get("avatar_url"),
        }
        for uid, profile in (profiles or {}).items()
    }


def _attach_grant_profile(
    grant: Dict[str, Any], cache: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """Copy a grant dict with ``username``/``avatar_url`` attached (best-effort)."""
    grant = dict(grant)
    profile = cache.get(str(grant.get("subject_id")))
    if profile:
        grant["username"] = profile.get("username")
        grant["avatar_url"] = profile.get("avatar_url")
    return grant


def _items_response(
    items: List[Dict[str, Any]], total: int, has_more: bool
) -> StashItemListResponse:
    """Build a list response, resolving grantee identities once per batch."""
    subject_ids = [
        grant.get("subject_id")
        for item in items
        for grant in (item.get("grants") or [])
    ]
    cache = _grant_profile_cache(subject_ids)
    if not cache:
        return StashItemListResponse(
            items=[_item_response(item) for item in items],
            total=total,
            has_more=has_more,
        )
    enriched = []
    for item in items:
        item = dict(item)
        if item.get("grants"):
            item["grants"] = [_attach_grant_profile(g, cache) for g in item["grants"]]
        enriched.append(StashItemResponse.model_validate(item))
    return StashItemListResponse(items=enriched, total=total, has_more=has_more)


def _grant_response(model_cls: type, grant: Dict[str, Any]) -> Any:
    """Serialize a single grant with its grantee identity resolved."""
    cache = _grant_profile_cache([grant.get("subject_id")])
    return model_cls.model_validate(_attach_grant_profile(grant, cache))
