"""
Admin licensing routes for managing instance license state.
"""

from fastapi import APIRouter, Request, HTTPException
from datetime import datetime

from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger

router = APIRouter()


@router.get("/license/status")
async def get_license_status(request: Request):
    """
    Retrieve current license status and validation state.
    """
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        import importlib

        _licensing = importlib.import_module("src.utils.common-utils.utils.licensing")

        instance_id = _licensing.get_instance_id()
        valid = _licensing.is_valid()
        free_tier = _licensing.is_free_tier()

        expiry_ts = _licensing.get_expiry_timestamp()
        expiry_date = None
        days_remaining = None
        if expiry_ts:
            expiry_date = datetime.fromtimestamp(expiry_ts).isoformat()
            days_remaining = max(
                0, int((expiry_ts - datetime.now().timestamp()) / 86400)
            )

        validation_result = _licensing.get_validation_result()

        return {
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


@router.get("/license/features")
async def get_license_features(request: Request):
    """
    Retrieve feature matrix showing all licensed features and their state.
    """
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        import importlib

        _licensing = importlib.import_module("src.utils.common-utils.utils.licensing")

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
            enabled = _licensing.has_feature(feature["key"])
            config = _licensing.get_feature_config(feature["key"])
            limit = _licensing.get_feature_limit(feature["key"])

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


@router.post("/license/validate")
async def validate_license(request: Request):
    """
    Validate a proposed license payload without applying it.
    """
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        body = await request.json()
        license_payload = body.get("license_key")

        if not license_payload:
            raise HTTPException(status_code=400, detail="license_key is required")

        import base64

        try:
            decoded = base64.b64decode(license_payload)
        except Exception:
            return {"valid": False, "error": "Invalid base64 encoding"}

        import importlib

        _licensing = importlib.import_module("src.utils.common-utils.utils.licensing")
        result = _licensing.validate_license_payload(decoded)

        return {"valid": result.get("valid", False), "details": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"License validation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/license/apply")
async def apply_license(request: Request):
    """
    Apply a new license from base64-encoded payload.
    """
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        body = await request.json()
        license_payload = body.get("license_key")

        if not license_payload:
            raise HTTPException(status_code=400, detail="license_key is required")

        import importlib

        _licensing = importlib.import_module("src.utils.common-utils.utils.licensing")

        current_valid = _licensing.is_valid()

        result = _licensing.apply_license_from_base64(license_payload)

        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "Failed to apply license"),
            }

        new_valid = _licensing.is_valid()

        if _admin:
            try:
                import src.api as api_mod

                db = api_mod.get_db()
                from src.core.admin.logging import AdminLogEntry, get_admin_logger

                admin_logger = get_admin_logger()
                entry = AdminLogEntry(
                    admin_id=_admin,
                    action="license.apply",
                    target_type="license",
                    details=f"License applied (was valid: {current_valid}, now valid: {new_valid})",
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                )
                admin_logger.log_action(db, entry)
            except Exception as le:
                logger.warning(f"Failed to log license change: {le}")

        return {"success": True, "valid": new_valid}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"License apply error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/license/reload")
async def reload_license(request: Request):
    """
    Reload license from environment variable or file without restart.
    """
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        import importlib

        _licensing = importlib.import_module("src.utils.common-utils.utils.licensing")

        current_valid = _licensing.is_valid()

        result = _licensing.reload_license()

        new_valid = _licensing.is_valid()

        if _admin:
            try:
                import src.api as api_mod

                db = api_mod.get_db()
                from src.core.admin.logging import AdminLogEntry, get_admin_logger

                admin_logger = get_admin_logger()
                entry = AdminLogEntry(
                    admin_id=_admin,
                    action="license.reload",
                    target_type="license",
                    details=f"License reloaded (was valid: {current_valid}, now valid: {new_valid})",
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                )
                admin_logger.log_action(db, entry)
            except Exception as le:
                logger.warning(f"Failed to log license reload: {le}")

        return {"success": result, "valid": new_valid}
    except Exception as e:
        logger.error(f"License reload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
