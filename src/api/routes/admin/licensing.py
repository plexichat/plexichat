"""
Admin licensing routes for managing instance license state.
"""

from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger
import utils.licensing as licensing_module

router = APIRouter(prefix="/license", tags=["Admin", "Licensing"])


class LicenseKey(BaseModel):
    license_key: str


@router.get("/status")
async def get_license_status(request: Request):
    """
    Retrieve current license status and validation state.
    """
    check_host_restriction(request)
    _admin_id = get_admin_from_token(request)

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
            expiry_date = datetime.fromtimestamp(expiry_ts).isoformat()
            days_remaining = max(
                0, int((expiry_ts - datetime.now().timestamp()) / 86400)
            )

        validation_result = licensing_module.get_validation_result()

        # Merge status_data with extra details
        return {
            **status_data,
            "valid": valid,
            "free_tier": free_tier,
            "tier": "free" if free_tier else "enterprise",
            "instance_id": instance_id,
            "expiry_timestamp": expiry_ts,
            "expiry_date": expiry_date,
            "days_remaining": days_remaining,
            "validation": validation_result,
        }
    except Exception as e:
        logger.error(f"License status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/features")
async def get_license_features(request: Request):
    """
    Retrieve feature matrix showing all licensed features and their state.
    """
    check_host_restriction(request)
    _admin_id = get_admin_from_token(request)

    try:
        features = [
            {"key": "bots", "label": "Bot Platform"},
            {"key": "premium", "label": "Premium Bot Limits"},
            {"key": "bond", "label": "PlexiBond"},
            {"key": "sso", "label": "SSO / SAML"},
            {"key": "advanced_automod", "label": "Advanced AutoMod"},
            {"key": "plexijoin", "label": "PlexiJoin Federation"},
            {"key": "audit_export", "label": "Audit Log Export"},
            {"key": "custom_roles", "label": "Custom Roles"},
        ]

        feature_matrix = []
        for feature in features:
            enabled = licensing_module.has_feature(feature["key"])
            config = licensing_module.get_feature_config(feature["key"])
            limit = licensing_module.get_feature_limit(feature["key"])

            feature_matrix.append(
                {
                    "key": feature["key"],
                    "label": feature["label"],
                    "enabled": enabled,
                    "config": config,
                    "limit": limit,
                }
            )

        return {"features": feature_matrix}
    except Exception as e:
        logger.error(f"License features error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate")
async def validate_license_key(request: Request, body: LicenseKey):
    """
    Validate a proposed license payload without applying it.
    """
    check_host_restriction(request)
    _admin_id = get_admin_from_token(request)

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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply")
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
    _admin_id = get_admin_from_token(request)

    raw_key = getattr(body, "license_key", None) if body else None
    if not raw_key:
        return {
            "message": "No license_key supplied; staying in current license state",
            "applied": False,
            "free_tier": licensing_module.is_free_tier(),
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
        if licensing_module.is_free_tier():
            return {
                "message": "License payload could not be validated; staying in free tier",
                "applied": False,
                "free_tier": True,
                "error": result.get("error"),
            }
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error") or "Failed to apply license",
        )
    return {"message": "License applied successfully", "applied": True}


@router.post("/check")
async def force_validate_license(request: Request):
    """
    Force a license validation check of the current license.

    In free-tier / unconfigured installs this is a no-op that reports the
    current state; only a real, broken license triggers a 400.
    """
    check_host_restriction(request)
    _admin_id = get_admin_from_token(request)

    if licensing_module.is_free_tier():
        return {
            "message": "License check completed (free tier)",
            "valid": True,
            "free_tier": True,
        }

    success = licensing_module.reload_license()
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="License validation failed",
        )
    return {"message": "License validated successfully"}
