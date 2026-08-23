"""
Configuration and capabilities routes - Expose public server constants.
"""

from fastapi import APIRouter, HTTPException, status
import utils.config as config
import utils.logger as logger
import utils.version as version_util
from src.api.schemas.config import ServerCapabilitiesResponse, AvatarConfigResponse
from src.api.schemas.common import ErrorResponse
from src.api.routes.auth.oauth_config import OAUTH_PROVIDERS

router = APIRouter(tags=["System"])


@router.get(
    "/capabilities",
    operation_id="get_server_capabilities",
    response_model=ServerCapabilitiesResponse,
    summary="Get server capabilities",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_capabilities() -> ServerCapabilitiesResponse:
    """
    Get global server capabilities and configuration constants.

    This includes UI constants like avatar color palettes, size limits,
    and supported features.
    """
    try:
        avatar_cfg = config.get("avatars", {})
        auth_cfg = config.get("authentication", {}) or {}
        oauth_cfg = config.get("oauth", {}) or {}
        access_required = False
        try:
            from src.core import auth

            access_required = auth.is_api_access_token_required()
        except Exception:
            access_required = False

        try:
            server_version = version_util.current_string()
        except Exception:
            server_version = None

        # Passkey availability mirrors the auth module's own configuration.
        passkey_enabled = bool(
            (auth_cfg.get("passkeys", {}) or {}).get("enabled", False)
        )

        # Advertise only providers that actually have credentials configured,
        # so the web client never shows a button that would 500 at /login.
        oauth_providers = {
            provider: bool((oauth_cfg.get(provider, {}) or {}).get("client_id"))
            for provider in OAUTH_PROVIDERS
        }

        accounts_cfg = auth_cfg.get("accounts", {}) or {}
        if not accounts_cfg.get("age_gate_enabled", False):
            age_gate_mode = "none"
        elif accounts_cfg.get("age_verification_type", "boolean") == "dob":
            age_gate_mode = "dob"
        else:
            age_gate_mode = "checkbox"

        return ServerCapabilitiesResponse(
            avatars=AvatarConfigResponse(
                default_colors=avatar_cfg.get(
                    "default_colors",
                    ["#e94560", "#4ade80", "#fbbf24", "#60a5fa", "#a78bfa", "#f472b6"],
                ),
                max_size=avatar_cfg.get("max_size", 512),
                max_file_size=avatar_cfg.get("max_file_size", 5 * 1024 * 1024),
                allowed_types=avatar_cfg.get(
                    "allowed_types",
                    ["image/jpeg", "image/png", "image/gif", "image/webp"],
                ),
            ),
            access_token_required=access_required,
            server_version=server_version,
            passkey_enabled=passkey_enabled,
            oauth_providers=oauth_providers,
            age_gate_mode=age_gate_mode,
        )
    except Exception as e:
        logger.error(f"Failed to fetch server capabilities: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
