import httpx
from typing import List
from urllib.parse import urlencode
from fastapi import APIRouter, Request, HTTPException

import src.api as api
import utils.logger as logger
from src.api.schemas.auth import OAuthLoginResponse, OAuthCallbackRequest, LoginResponse
from src.api.schemas.common import ErrorResponse
from .helpers import _user_to_response
from .oauth_config import OAUTH_PROVIDERS
from src.core.auth.oauth import (
    create_oauth_state,
    verify_oauth_state,
    generate_pkce_pair,
    verify_pkce,
)

try:
    import utils.config as config_util
except ImportError:
    config_util = None

try:
    from src.utils.net import get_client_ip as _oauth_get_client_ip
except ImportError:  # pragma: no cover
    _oauth_get_client_ip = None

router = APIRouter()


# ----------------------------- redirect_uri allow-list -----------------------------
#
# SECURITY: ``redirect_uri`` is supplied by the caller (effectively the
# browser) and is later round-tripped to the IdP and back to us. If we
# accept any URI the user gives us we hand them an open redirector
# and a token-replay primitive.
#
# The allow-list is built once at module load from two sources:
#   1. ``oauth.<provider>.allowed_redirect_uris`` config list.
#   2. The default localhost dev origin (127.0.0.1:8000 / [::1]:8000)
#      which is only accepted when ``oauth.allow_localhost_redirects``
#      is true (default: true for dev builds).
def _provider_allowed_redirects(provider: str) -> List[str]:
    if not config_util:
        return []
    oauth_cfg = config_util.get("oauth", {}) or {}
    provider_cfg = oauth_cfg.get(provider, {}) or {}
    out = list(provider_cfg.get("allowed_redirect_uris", []) or [])
    if oauth_cfg.get("allow_localhost_redirects", True):
        out.extend(
            [
                "http://127.0.0.1:8000",
                "http://localhost:8000",
                "https://127.0.0.1:8000",
                "https://localhost:8000",
            ]
        )
    return out


def _redirect_uri_is_allowed(provider: str, redirect_uri: str) -> bool:
    """Strict exact-match allow-list comparison.

    No substring matching, no glob, no ``startswith`` — a single
    mistyped character MUST reject. URL normalisation is done by
    canonicalising trailing slashes only.
    """
    if not redirect_uri:
        return False
    target = redirect_uri.rstrip("/")
    for candidate in _provider_allowed_redirects(provider):
        if candidate.rstrip("/") == target:
            return True
    return False


@router.get(
    "/oauth/{provider}/login",
    response_model=OAuthLoginResponse,
    summary="Initiate OAuth login",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid provider"},
        429: {"model": ErrorResponse, "description": "Too many pending OAuth requests"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def oauth_login_init(
    request: Request, provider: str, redirect_uri: str
) -> OAuthLoginResponse:
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Unsupported OAuth provider: {provider}",
                }
            },
        )

    if not _redirect_uri_is_allowed(provider, redirect_uri):
        from utils import (
            logger as _oauth_logger,
        )  # local alias to keep import surface small

        _oauth_logger.warning(
            f"OAuth login rejected: redirect_uri={redirect_uri!r} "
            f"not on the allow-list for provider {provider}"
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": "redirect_uri is not on the allow-list for this provider",
                }
            },
        )

    oauth_config = config_util.get("oauth", {}) if config_util else {}
    provider_config = oauth_config.get(provider, {})

    client_id = provider_config.get("client_id")
    if not client_id:
        logger.error(f"OAuth client_id not configured for {provider}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": 500,
                    "message": f"OAuth not configured for {provider}",
                }
            },
        )

    provider_info = OAUTH_PROVIDERS[provider]
    # SECURITY: prefer the trusted-proxy-aware ``get_client_ip()``
    # helper so X-Forwarded-For is honoured only for trusted peers
    # and never spoofable from the open internet. Fall back to
    # ``request.client.host`` only when the helper is unavailable.
    if _oauth_get_client_ip is not None:
        ip_address = _oauth_get_client_ip(request)
    else:
        ip_address = request.client.host if request.client else None

    pkce_enabled = oauth_config.get("pkce_enabled", True) and provider_info.get(
        "supports_pkce", False
    )

    pkce_challenge = None
    code_verifier = None
    if pkce_enabled:
        pkce_config = oauth_config.get("pkce", {})
        pkce = generate_pkce_pair(config=pkce_config)
        pkce_challenge = pkce.code_challenge
        code_verifier = pkce.code_verifier

    include_nonce = provider_info.get("supports_nonce", False)
    oauth_state = create_oauth_state(
        provider=provider,
        redirect_uri=redirect_uri,
        include_nonce=include_nonce,
        pkce_challenge=pkce_challenge,
        ip_address=ip_address,
    )

    if not oauth_state:
        logger.warning(f"Failed to create OAuth state for {provider} from {ip_address}")
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": "Too many pending OAuth requests. Please try again later.",
                }
            },
        )

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider_info["scopes"]),
        "state": oauth_state.state_token,
    }

    if pkce_enabled and pkce_challenge:
        params["code_challenge"] = pkce_challenge
        params["code_challenge_method"] = "S256"

    if include_nonce and oauth_state.nonce_value:
        params["nonce"] = oauth_state.nonce_value

    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "select_account"

    query_string = urlencode(params)
    auth_url = f"{provider_info['auth_url']}?{query_string}"

    logger.info(f"OAuth login initiated for {provider} from {ip_address}")

    response = OAuthLoginResponse(url=auth_url, state=oauth_state.state_token or "")

    if code_verifier:
        response.code_verifier = code_verifier

    return response


@router.post(
    "/oauth/{provider}/callback",
    response_model=LoginResponse,
    summary="OAuth callback",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request or provider"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def oauth_callback(
    request: Request,
    provider: str,
    callback_data: OAuthCallbackRequest,
) -> LoginResponse:
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Unsupported OAuth provider: {provider}",
                }
            },
        )

    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module unavailable"}},
        )

    if not _redirect_uri_is_allowed(provider, callback_data.redirect_uri):
        logger.warning(
            f"OAuth callback rejected: redirect_uri={callback_data.redirect_uri!r} "
            f"not on the allow-list for provider {provider}"
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": "redirect_uri is not on the allow-list for this provider",
                }
            },
        )

    valid, state_record, error_msg = verify_oauth_state(
        state_token=callback_data.state,
        provider=provider,
        redirect_uri=callback_data.redirect_uri,
    )

    if not valid:
        logger.warning(f"OAuth state verification failed for {provider}: {error_msg}")
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Security verification failed: {error_msg}",
                }
            },
        )

    if state_record and state_record.pkce_challenge:
        if not callback_data.code_verifier:
            logger.warning(
                f"OAuth PKCE verification failed for {provider}: missing code_verifier"
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "PKCE code_verifier required"}
                },
            )
        oauth_config = config_util.get("oauth", {}) if config_util else {}
        pkce_config = oauth_config.get("pkce", {})
        if not verify_pkce(
            callback_data.code_verifier, state_record.pkce_challenge, config=pkce_config
        ):
            logger.warning(
                f"OAuth PKCE verification failed for {provider}: invalid code_verifier"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "PKCE verification failed"}},
            )

    oauth_config = config_util.get("oauth", {}) if config_util else {}
    provider_config = oauth_config.get(provider, {})
    client_id = provider_config.get("client_id")
    client_secret = provider_config.get("client_secret")

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": 500,
                    "message": f"OAuth not configured for {provider}",
                }
            },
        )

    provider_info = OAUTH_PROVIDERS[provider]

    async with httpx.AsyncClient() as client:
        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": callback_data.code,
            "grant_type": "authorization_code",
            "redirect_uri": callback_data.redirect_uri,
        }

        if callback_data.code_verifier and state_record and state_record.pkce_challenge:
            token_data["code_verifier"] = callback_data.code_verifier

        headers = {"Accept": "application/json"}

        try:
            token_resp = await client.post(
                provider_info["token_url"], data=token_data, headers=headers
            )
            token_resp.raise_for_status()
            token_result = token_resp.json()
            access_token = token_result.get("access_token")
            if not access_token:
                logger.error(
                    f"Failed to get access token from {provider}: {token_result}"
                )
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": {"code": 401, "message": "Failed to get access token"}
                    },
                )
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Token exchange failed with {provider}: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Token exchange failed"}},
            )
        except Exception as e:
            logger.error(f"Error during token exchange with {provider}: {e}")
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Token exchange failed"}},
            )

        user_info_headers = {"Authorization": f"Bearer {access_token}"}
        try:
            user_info_resp = await client.get(
                provider_info["user_info_url"], headers=user_info_headers
            )
            user_info_resp.raise_for_status()
            user_info = user_info_resp.json()
        except Exception as e:
            logger.error(f"Error fetching user info from {provider}: {e}")
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Failed to fetch user info"}},
            )

        external_id = None
        email = None
        username_hint = None

        if provider == "google":
            external_id = user_info.get("sub")
            if not user_info.get("email_verified"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Google account email must be verified",
                        }
                    },
                )
            email = user_info.get("email")
            username_hint = user_info.get("name")
        elif provider == "github":
            external_id = str(user_info.get("id"))
            email = user_info.get("email") if user_info.get("verified") else None
            username_hint = user_info.get("login")
            if not email:
                try:
                    emails_resp = await client.get(
                        "https://api.github.com/user/emails", headers=user_info_headers
                    )
                    if emails_resp.status_code == 200:
                        emails = emails_resp.json()
                        primary_email = next(
                            (
                                e["email"]
                                for e in emails
                                if e.get("primary") and e.get("verified")
                            ),
                            None,
                        )
                        email = primary_email or next(
                            (e["email"] for e in emails if e.get("verified")),
                            None,
                        )
                except Exception:
                    pass
            if not email:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "GitHub account must expose a verified email",
                        }
                    },
                )
        elif provider == "microsoft":
            external_id = user_info.get("id")
            email = user_info.get("mail") or user_info.get("userPrincipalName")
            username_hint = user_info.get("displayName")

        if not external_id:
            logger.error(f"Could not identify user from {provider} response")
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Could not identify user"}},
            )

        # SECURITY: pull the IP through the hardened helper rather than
        # ``request.client.host`` so X-Forwarded-For spoofing attempts
        # outside the trusted-proxy perimeter cannot hijack audit logs.
        if _oauth_get_client_ip is not None:
            ip_address = _oauth_get_client_ip(request)
        else:
            ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("User-Agent")

        try:
            result = auth.oauth_login(
                provider=provider,
                external_id=external_id,
                email=email,
                username_hint=username_hint,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except Exception as e:
            logger.error(f"OAuth login failed for {provider}:{external_id}: {e}")
            raise HTTPException(
                status_code=401, detail={"error": {"code": 401, "message": str(e)}}
            )

        if result.status.value == "failed":
            error_code = 400 if "Age" in str(result.message) else 401
            raise HTTPException(
                status_code=error_code,
                detail={"error": {"code": error_code, "message": result.message}},
            )

        if result.status.value == "two_factor_required":
            return LoginResponse(
                status="two_factor_required",
                token=None,
                user=None,
                challenge_token=result.challenge_token,
                methods=result.methods,
                expires_in=result.expires_in,
            )

        logger.info(f"OAuth login successful for {provider}:{external_id}")
        return LoginResponse(
            status="success",
            token=result.token,
            user=_user_to_response(result.user) if result.user else None,
        )
