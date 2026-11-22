"""
Admin licensing routes for managing instance license state.
"""

import asyncio
import math
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel
from .utils import (
    check_host_restriction,
    require_admin_permission,
)
from src.api.schemas.common import ErrorResponse
import utils.logger as logger
import utils.licensing as licensing_module
from src.core.admin.logging import AdminLogEntry, get_admin_logger

router = APIRouter(prefix="/license", tags=["Admin", "Licensing"])

# The license surface is privileged: the status/feature reads expose the
# instance id, validation result, expiry and allowed-host list, and the
# validation endpoint accepts arbitrary license payloads. Authentication alone
# used to be enough for those three reads (they called ``get_admin_from_token``,
# which only validates the session), so any admin holding a valid session --
# including a read-only/low-privilege role -- could enumerate all of it. A
# single scope, shared with the mutation endpoints, now gates every license
# route so the whole module has one authorization boundary that cannot drift.
_LICENSE_SCOPE = "admin.license.manage"


# GET /admin/license/features rebuilds the whole license feature matrix on every
# admin-panel poll: a fixed catalogue of features, three licensing accessors each
# (~48 calls for the 16 entries below). Every accessor re-enters the license
# manager's ``_ensure_validated()``, which performs full signature/expiry
# validation on a cold process, so the first poll after a restart is slow and
# every later poll repeats the same static work.
#
# The matrix is process-wide and derived only from license state, which changes
# rarely and never mid-request, so cache the fully assembled response in a
# single-entry, TTL-bounded slot. There is exactly one matrix per process, so a
# single entry *is* the cache bound - it cannot grow with request volume or with
# hostile input. The TTL bounds display staleness, and both license mutation
# routes explicitly invalidate below after a hot-swap, so a new license is
# visible immediately. Caching cannot weaken authorization: both admin gates
# run in the route *before* the cache is consulted, and the licensing accessors
# used for real feature enforcement elsewhere are untouched.
_FEATURE_CATALOG_TTL_SECONDS = 30.0

# Immutable module data so a request does not even rebuild the label list.
_LICENSE_FEATURES: Tuple[Tuple[str, str], ...] = (
    ("bots", "Bot Platform"),
    ("premium", "Premium Bot Limits"),
    ("bond", "PlexiBond"),
    ("sso", "SSO / SAML"),
    ("advanced_automod", "Advanced AutoMod"),
    ("plexijoin", "PlexiJoin Federation"),
    ("audit_export", "Audit Log Export"),
    ("custom_roles", "Custom Roles"),
    ("artifacts", "PlexiSuite Artifacts"),
    ("plexiscribe", "PlexiScribe"),
    ("plexiscript", "PlexiScript"),
    ("artifacts_whiteboard", "PlexiBoard (Whiteboard)"),
    ("voice_transcription", "Voice Transcription"),
    ("plexistash", "PlexiStash"),
    ("plexisum", "PlexiSum"),
    ("all", "All Features (meta)"),
)

_feature_catalog_cache: Optional[Tuple[float, Dict[str, Any]]] = None
_feature_catalog_lock = threading.Lock()


def _build_license_feature_payload() -> Dict[str, Any]:
    """Assemble the feature matrix from the live license state."""
    feature_matrix = []
    for key, label in _LICENSE_FEATURES:
        feature_matrix.append(
            {
                "key": key,
                "label": label,
                "enabled": licensing_module.has_feature(key),
                "config": licensing_module.get_feature_config(key),
                "limit": licensing_module.get_feature_limit(key),
            }
        )
    return {"features": feature_matrix}


def _clone_cached_value(value: Any) -> Any:
    """Copy only mutable containers; immutable license scalars are shared."""
    if isinstance(value, dict):
        return {key: _clone_cached_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone_cached_value(item) for item in value]
    return value


def _copy_license_feature_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Detach the cached payload so callers cannot mutate shared state.

    ``config``/``limit`` values come from the license document and may be nested
    containers, so copy those too. A targeted container clone (rather than
    ``copy.deepcopy``) keeps warmed reads cheap even in free-tier mode, where
    the accessors themselves are trivial.
    """
    return {
        "features": [
            {
                "key": row["key"],
                "label": row["label"],
                "enabled": row["enabled"],
                "config": _clone_cached_value(row["config"]),
                "limit": _clone_cached_value(row["limit"]),
            }
            for row in payload["features"]
        ]
    }


def _get_license_feature_payload() -> Dict[str, Any]:
    """Return the current feature matrix, recomputing at most once per TTL."""
    global _feature_catalog_cache
    now = time.monotonic()
    with _feature_catalog_lock:
        cached = _feature_catalog_cache
        if cached is not None and (now - cached[0]) < _FEATURE_CATALOG_TTL_SECONDS:
            return _copy_license_feature_payload(cached[1])
    # Build outside the lock: a cold build re-enters the license validator and
    # must not serialize concurrent callers. A rare double-build on a cold race
    # is harmless (both write the same value); the result is cached below.
    payload = _build_license_feature_payload()
    with _feature_catalog_lock:
        _feature_catalog_cache = (now, payload)
    return _copy_license_feature_payload(payload)


def clear_license_feature_catalog_cache() -> None:
    """Drop the memoized matrix (call after any license hot-swap)."""
    global _feature_catalog_cache
    with _feature_catalog_lock:
        _feature_catalog_cache = None


def _invalidate_license_derived_caches() -> None:
    """Drop every process-local verdict derived from the license document.

    A reload/hot-swap can change the effective entitlements on *every* path,
    including a failed reload (``reload_license`` leaves the manager holding the
    newly loaded, invalid license and demotes the module to free tier). Any
    cached AVAILABLE/granted verdict from the previous license must therefore be
    discarded even when the operation reports failure, or the capability gates
    keep handing out an entitlement the instance no longer holds. Local imports
    avoid import cycles with the route modules that consume these caches.
    """
    from src.core.artifacts.capabilities import clear_capability_cache

    clear_capability_cache()
    clear_license_feature_catalog_cache()
    # The launcher/admin capability *payload* caches are separate single-entry
    # memos built on top of the core verdicts; they must not outlive a swap.
    try:
        from src.api.routes.capabilities import clear_capabilities_payload_cache

        clear_capabilities_payload_cache()
    except Exception:
        pass
    try:
        from src.api.routes.admin.capabilities import (
            clear_admin_capabilities_payload_cache,
        )

        clear_admin_capabilities_payload_cache()
    except Exception:
        pass


def warm_license_feature_catalog_cache() -> None:
    """Prime the matrix off the request path (best-effort, never fatal).

    The first assembly runs three accessors per feature, each re-entering the
    license validator. Calling this from the router lifespan moves that one-off
    cost onto a worker thread so the first authenticated, scoped admin poll is a
    cache hit. The host-restriction and permission gates in
    ``get_license_features`` still run on every request; only the shared,
    caller-independent matrix is primed. A failure is logged and swallowed -
    the route rebuilds the payload on demand exactly as before.
    """
    try:
        _get_license_feature_payload()
    except Exception as e:
        logger.debug(f"License feature catalogue warm-up skipped: {e}")


@asynccontextmanager
async def _license_feature_catalog_lifespan(app: Any):
    """Run the cold matrix build at boot, off the event loop and off requests."""
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, warm_license_feature_catalog_cache
        )
    except Exception as e:
        logger.debug(f"License feature catalogue warm-up failed: {e}")
    yield


# FastAPI merges this into the application lifespan via include_router().
router.lifespan_context = _license_feature_catalog_lifespan


def _expiry_date_iso(expiry_ts: float) -> str:
    """Format an expiry timestamp as UTC ISO-8601."""
    return datetime.fromtimestamp(expiry_ts, tz=timezone.utc).isoformat()


def _days_remaining(expiry_ts: float, now_ts: Optional[float] = None) -> int:
    """Whole days remaining, rounding partial days up.

    Any positive remainder counts as a full day so 23h59m left reports
    1 rather than 0. Expired timestamps report 0.
    """
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()
    remaining = expiry_ts - now_ts
    if remaining <= 0:
        return 0
    return math.ceil(remaining / 86400)


class LicenseKey(BaseModel):
    license_key: str


def _audit_license_action(
    request: Request,
    admin_id: int,
    action: str,
    status: str,
    details: Optional[str] = None,
    current_state: Optional[str] = None,
    new_state: Optional[str] = None,
) -> None:
    """Write a license action to the admin audit log."""
    try:
        import src.api as api_mod

        db = api_mod.get_db()
        if db is None:
            return
        ip = request.client.host if request.client else "unknown"
        ua = request.headers.get("user-agent", "")
        entry = AdminLogEntry(
            admin_id=admin_id,
            action=action,
            status=status,
            details=details,
            ip_address=ip,
            user_agent=ua,
        )
        if current_state or new_state:
            extra = {}
            if current_state:
                extra["current_license"] = current_state
            if new_state:
                extra["new_license"] = new_state
            import json

            entry.details = (
                (details or "") + (" | " if details else "") + json.dumps(extra)
            )
        get_admin_logger().log_action(db, entry)
    except Exception as exc:
        logger.debug(f"Failed to audit license action: {exc}")


@router.get(
    "/status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_license_status(request: Request):
    """
    Retrieve current license status and validation state.
    """
    check_host_restriction(request)
    require_admin_permission(request, _LICENSE_SCOPE)

    try:
        status_data = licensing_module.to_dict()

        # Add extra details as seen in backup
        instance_id = licensing_module.get_instance_id()
        valid = licensing_module.is_valid()
        free_tier = licensing_module.is_free_tier()

        expiry_ts = licensing_module.get_expiry_timestamp()
        expiry_date = None
        days_remaining = None
        if expiry_ts:
            expiry_date = _expiry_date_iso(expiry_ts)
            days_remaining = _days_remaining(expiry_ts)

        validation_result = licensing_module.get_validation_result()

        provisional = licensing_module.is_provisional()
        tier = "provisional" if provisional else ("free" if free_tier else "enterprise")

        # Merge status_data with extra details
        return {
            **status_data,
            "valid": valid,
            "free_tier": free_tier,
            "provisional": provisional,
            "license_type": licensing_module.get_license_type(),
            "allowed_hosts": licensing_module.get_allowed_hosts(),
            "tier": tier,
            "instance_id": instance_id,
            "expiry_timestamp": expiry_ts,
            "expiry_date": expiry_date,
            "days_remaining": days_remaining,
            "validation": validation_result,
        }
    except Exception as e:
        logger.error(f"License status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/features",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_license_features(request: Request):
    """
    Retrieve feature matrix showing all licensed features and their state.
    """
    # Security gates stay first and unchanged: host restriction + the shared
    # admin.license.manage scope. A warm cache never short-circuits either one.
    check_host_restriction(request)
    require_admin_permission(request, _LICENSE_SCOPE)

    try:
        # The matrix is user-independent; a detached copy is returned so the
        # cached entry is never mutated by a caller.
        return _get_license_feature_payload()
    except Exception as e:
        logger.error(f"License features error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/validate",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def validate_license_key(request: Request, body: LicenseKey):
    """
    Validate a proposed license payload without applying it.
    """
    check_host_restriction(request)
    require_admin_permission(request, _LICENSE_SCOPE)

    try:
        license_payload = body.license_key

        if not license_payload:
            raise HTTPException(status_code=400, detail="license_key is required")

        import base64

        try:
            decoded = base64.b64decode(license_payload)
        except Exception:
            return {"valid": False, "error": "Invalid base64 encoding"}

        result = licensing_module.validate_license_payload(decoded)

        return {"valid": result.get("valid", False), "details": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"License validation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/apply",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def apply_license(request: Request, body: Optional[LicenseKey] = None):
    """
    Apply a new license key.

    The license is applied in-memory only and is NOT persisted to disk.
    On server restart the original license file on disk is re-read.
    For permanent changes, update the license file at ~/.plexichat/config/license
    (or license.json) and call POST /api/v1/admin/license/check to reload it.

    If no body is provided, or the supplied key is empty / not a valid
    base64-encoded license payload (free-tier / dev mode), the call is
    treated as a no-op and reports the current state, so the endpoint
    stays idempotent for the self-test pipeline.
    """
    check_host_restriction(request)
    _admin_id = require_admin_permission(request, _LICENSE_SCOPE)

    # Capture current state before applying
    before_instance = licensing_module.get_instance_id()
    before_valid = licensing_module.is_valid()
    before_free = licensing_module.is_free_tier()

    raw_key = getattr(body, "license_key", None) if body else None
    if not raw_key:
        _audit_license_action(
            request,
            _admin_id,
            "license.apply",
            "noop",
            details="Empty license_key; no change",
            current_state=before_instance,
        )
        return {
            "message": "No license_key supplied; staying in current license state",
            "applied": False,
            "free_tier": before_free,
        }

    import base64
    import json

    try:
        decoded = base64.b64decode(raw_key, validate=True)
    except Exception:
        return {
            "message": "license_key is not a valid base64 payload; staying in current license state",
            "applied": False,
            "free_tier": licensing_module.is_free_tier(),
        }

    # A valid Plexichat license payload is a UTF-8 JSON document. Anything
    # else is a placeholder / random string from the self-test generator,
    # so we treat it as a no-op rather than failing the endpoint.
    try:
        decoded_text = decoded.decode("utf-8")
        json.loads(decoded_text)
    except (UnicodeDecodeError, ValueError):
        return {
            "message": "license_key is not a valid license JSON payload; staying in current license state",
            "applied": False,
            "free_tier": licensing_module.is_free_tier(),
        }

    result = licensing_module.apply_license_from_base64(raw_key)
    if not result.get("success"):
        # A failed apply is a no-op: the transactional apply leaves the
        # current license (free-tier or licensed) untouched, so report that
        # state rather than demoting to free tier or erroring. This matches
        # the documented contract that an invalid license_key returns the
        # current state without error and is audited as a failed no-op.
        _audit_license_action(
            request,
            _admin_id,
            "license.apply",
            "failed",
            details=result.get("error")
            or "License validation failed; current license unchanged",
            current_state=before_instance,
        )
        return {
            "message": "License payload could not be validated; staying in current license state",
            "applied": False,
            "free_tier": licensing_module.is_free_tier(),
            "error": result.get("error"),
        }
    after_state = licensing_module.to_dict()
    after_valid = licensing_module.is_valid()
    after_free = licensing_module.is_free_tier()
    after_instance = licensing_module.get_instance_id()
    after_expiry = licensing_module.get_expiry_timestamp()
    after_features = after_state.get("features", {})
    after_limits = after_state.get("limits", {})
    # Feature/capability availability is derived from the license, so drop every
    # memoized verdict before the audit trail records the swap.
    _invalidate_license_derived_caches()
    _audit_license_action(
        request,
        _admin_id,
        "license.apply",
        "success",
        details=f"Applied license for instance: {after_instance}",
        current_state=before_instance,
        new_state=after_instance,
    )
    return {
        "message": "License applied successfully",
        "applied": True,
        "before": {
            "instance_id": before_instance,
            "valid": before_valid,
            "free_tier": before_free,
        },
        "after": {
            "instance_id": after_instance,
            "valid": after_valid,
            "free_tier": after_free,
            "expiry": after_expiry,
            "features": after_features,
            "limits": after_limits,
        },
    }


@router.post(
    "/check",
    responses={
        400: {"model": ErrorResponse, "description": "License validation failed"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def force_validate_license(request: Request):
    """
    Force a license validation check of the current license.

    In free-tier / unconfigured installs this is a no-op that reports the
    current state; only a real, broken license triggers a 400.
    """
    check_host_restriction(request)
    _admin_id = require_admin_permission(request, _LICENSE_SCOPE)

    if licensing_module.is_free_tier():
        _audit_license_action(
            request,
            _admin_id,
            "license.check",
            "noop",
            details="License check (free tier)",
        )
        return {
            "message": "License check completed (free tier)",
            "valid": True,
            "free_tier": True,
        }

    _ = licensing_module.is_valid()
    success = licensing_module.reload_license()
    # A failed reload is still a state change: the manager now holds the newly
    # loaded (invalid) license and the module is demoted to free tier. Invalidate
    # derived verdicts on *every* path so no cached AVAILABLE/grant survives the
    # reload, otherwise the capability gates keep granting a revoked entitlement.
    _invalidate_license_derived_caches()
    if not success:
        _audit_license_action(
            request,
            _admin_id,
            "license.check",
            "failed",
            details="License reload failed validation",
            current_state=licensing_module.get_instance_id(),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="License validation failed",
        )
    after_instance = licensing_module.get_instance_id()
    _audit_license_action(
        request,
        _admin_id,
        "license.check",
        "success",
        details=f"Reloaded license for instance: {after_instance}",
        current_state=licensing_module.get_instance_id(),
        new_state=after_instance,
    )
    return {"message": "License validated successfully"}
