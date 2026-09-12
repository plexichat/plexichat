import hmac as hmac_module
import re
from typing import List, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response

import src.api as api
import utils.logger as logger
from src.api.schemas.auth import OAuthLoginResponse, OAuthCallbackRequest, LoginResponse
from src.api.schemas.common import ErrorResponse
from src.core.auth.models import AuthStatus
from .helpers import _user_to_response
from .cookies import (
    set_session_cookie,
    session_cookie_secure,
    session_cookie_max_age,
    session_token_for_response,
)
from .oauth_config import OAUTH_PROVIDERS
from src.core.auth.oauth import (
    create_oauth_state,
    verify_oauth_state,
    verify_oauth_nonce,
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
#      is explicitly enabled (opt-in; disabled by default so production
#      deployments do not silently accept dev loopback redirects).
def _provider_allowed_redirects(provider: str) -> List[str]:
    if not config_util:
        return []
    oauth_cfg = config_util.get("oauth", {}) or {}
    provider_cfg = oauth_cfg.get(provider, {}) or {}
    out = list(provider_cfg.get("allowed_redirect_uris", []) or [])
    if oauth_cfg.get("allow_localhost_redirects", False):
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


# RFC 7636 code_challenge grammar: 43-128 unreserved characters.
_PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")

# Short-lived HttpOnly cookie binding the OAuth state to the browser that
# started the flow (mitigates login CSRF: an attacker page can supply a
# state value in the callback POST, but cannot plant the matching cookie
# on our origin). Scoped tightly to the OAuth endpoints.
OAUTH_STATE_COOKIE = "plexichat_oauth_state"


def _oauth_cookie_params(max_age: int) -> dict:
    from src.api.config import get_api_config

    try:
        api_prefix = get_api_config().api_prefix.rstrip("/") or "/api/v1"
    except Exception:
        api_prefix = "/api/v1"
    return {
        "max_age": max_age,
        "httponly": True,
        "secure": session_cookie_secure(),
        "samesite": "lax",
        # Covers both /auth/oauth/{provider}/login and .../callback.
        "path": f"{api_prefix}/auth/oauth",
    }


@router.get(
    "/oauth/{provider}/login",
    response_model=OAuthLoginResponse,
    summary="Initiate OAuth login",
    responses={
        400: {"model": ErrorResponse, "description": "redirect_uri not allowed"},
        404: {"model": ErrorResponse, "description": "Unsupported OAuth provider"},
        429: {"model": ErrorResponse, "description": "Too many pending OAuth requests"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def oauth_login_init(
    request: Request,
    response: Response,
    provider: str,
    redirect_uri: str,
    code_challenge: Optional[str] = None,
    code_challenge_method: Optional[str] = None,
) -> OAuthLoginResponse:
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": 404,
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
    if pkce_enabled:
        # SECURITY: the public client (SPA) must generate its own PKCE pair —
        # the verifier is a secret only the browser should ever hold, so the
        # server accepts only the S256 challenge and never mints verifiers.
        client_challenge = (code_challenge or "").strip()
        if code_challenge_method not in (None, "S256"):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Only the S256 code_challenge_method is supported",
                    }
                },
            )
        if not client_challenge:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "code_challenge is required for PKCE OAuth flows",
                    }
                },
            )
        if not _PKCE_CHALLENGE_RE.fullmatch(client_challenge):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid code_challenge format"}
                },
            )
        pkce_challenge = client_challenge

    state_cookie_required = True
    if config_util:
        try:
            state_cookie_required = bool(
                (config_util.get("oauth", {}) or {}).get("state_cookie_required", True)
            )
        except Exception:
            pass

    include_nonce = provider_info.get("supports_nonce", False)
    issue_state_cookie = state_cookie_required
    oauth_state = create_oauth_state(
        provider=provider,
        redirect_uri=redirect_uri,
        include_nonce=include_nonce,
        pkce_challenge=pkce_challenge,
        ip_address=ip_address,
        cookie_issued=issue_state_cookie,
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

    response_model = OAuthLoginResponse(
        url=auth_url,
        auth_url=auth_url,
        state=oauth_state.state_token or "",
    )

    # Bind the state token to this browser via a short-lived HttpOnly cookie
    # scoped to the OAuth endpoints; the callback requires it to match.
    if issue_state_cookie and oauth_state.state_token:
        ttl = 600
        try:
            oauth_cfg = (config_util.get("oauth", {}) or {}) if config_util else {}
            ttl = int(oauth_cfg.get("state_ttl_seconds", 600))
        except Exception:
            pass
        params_cookie = _oauth_cookie_params(max_age=max(60, ttl))
        response.set_cookie(
            OAUTH_STATE_COOKIE, oauth_state.state_token, **params_cookie
        )

    return response_model


@router.post(
    "/oauth/{provider}/callback",
    response_model=LoginResponse,
    summary="OAuth callback",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        404: {"model": ErrorResponse, "description": "Unsupported OAuth provider"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def oauth_callback(
    request: Request,
    response: Response,
    provider: str,
    callback_data: OAuthCallbackRequest,
) -> LoginResponse:
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": 404,
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
        response.delete_cookie(
            OAUTH_STATE_COOKIE, path=_oauth_cookie_params(60)["path"]
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Security verification failed: {error_msg}",
                }
            },
        )

    # Login-CSRF defense (enforced): when this flow started in a browser it
    # carries a state-binding HttpOnly cookie that MUST match. A mismatching
    # or missing cookie means some other party supplied the state value.
    if state_record and state_record.cookie_issued:
        state_cookie = request.cookies.get(OAUTH_STATE_COOKIE)
        if not state_cookie or not hmac_module.compare_digest(
            state_cookie, callback_data.state or ""
        ):
            logger.warning(
                f"OAuth callback rejected for {provider}: state cookie "
                f"{'mismatch' if state_cookie else 'missing'}"
            )
            response.delete_cookie(
                OAUTH_STATE_COOKIE, path=_oauth_cookie_params(60)["path"]
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Security verification failed: state cookie mismatch",
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

        # M6: the nonce we sent to the IdP must be checked against the ID
        # token it returns, otherwise the nonce is decoration with no
        # consumer. OIDC binds the nonce to the authorization session, so a
        # stolen/misrouted authorization code can no longer be exchanged into
        # a session minted for a different browser. The raw nonce is never
        # persisted — only its SHA-256 hash rides with the state record — so
        # the claim is re-hashed and compared in constant time via
        # verify_oauth_nonce. The state was already atomically consumed
        # (single-use UPDATE ... WHERE used = 0) by verify_oauth_state above,
        # so a failed nonce leaves no replayable state behind. Fail closed:
        # a nonce-bound flow without a matching id_token nonce is rejected.
        # Placed OUTSIDE the token exchange try/except so an HTTPException is
        # not remapped to a generic token-exchange failure.
        id_token = token_result.get("id_token")
        stored_nonce_hash = getattr(state_record, "nonce", None)
        legacy_nonce_raw = getattr(state_record, "nonce_value", None)
        if stored_nonce_hash or legacy_nonce_raw:
            import base64
            import json as _json
            import secrets as _secrets

            if not id_token or not isinstance(id_token, str):
                logger.warning(
                    f"OAuth nonce check failed for {provider}: "
                    f"nonce-bound flow returned no id_token"
                )
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": {
                            "code": 401,
                            "message": "OAuth nonce mismatch",
                        }
                    },
                )
            try:
                parts = id_token.split(".")
                if len(parts) < 2:
                    raise ValueError("malformed id_token")
                payload = parts[1]
                padding = "=" * (-len(payload) % 4)
                claims = _json.loads(base64.urlsafe_b64decode(payload + padding))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to decode id_token from {provider}: {e}")
                raise HTTPException(
                    status_code=401,
                    detail={"error": {"code": 401, "message": "Invalid id_token"}},
                )
            presented_nonce = claims.get("nonce") if isinstance(claims, dict) else None
            if stored_nonce_hash:
                nonce_ok = verify_oauth_nonce(stored_nonce_hash, presented_nonce)
            else:
                # Fallback for creation-time records carrying the raw value
                # (e.g. unit tests): constant-time direct comparison.
                nonce_ok = bool(
                    isinstance(presented_nonce, str)
                    and isinstance(legacy_nonce_raw, str)
                    and _secrets.compare_digest(presented_nonce, legacy_nonce_raw)
                )
            if not nonce_ok:
                logger.warning(
                    f"OAuth nonce mismatch for {provider} from "
                    f"{getattr(request, 'client', None)}"
                )
                raise HTTPException(
                    status_code=401,
                    detail={"error": {"code": 401, "message": "OAuth nonce mismatch"}},
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
        elif provider == "gitlab":
            # SECURITY: link by verified email only. GitLab signals email
            # verification through its OIDC userinfo endpoint (the ``openid``
            # scope is requested during authorization); fall back to the REST
            # /user endpoint where ``confirmed_at`` marks a confirmed address.
            try:
                oidc_resp = await client.get(
                    "https://gitlab.com/oauth/userinfo", headers=user_info_headers
                )
                if oidc_resp.status_code == 200:
                    oidc_info = oidc_resp.json()
                    if oidc_info.get("sub"):
                        external_id = str(oidc_info["sub"])
                    username_hint = oidc_info.get("nickname") or oidc_info.get("name")
                    if oidc_info.get("email_verified") and oidc_info.get("email"):
                        email = oidc_info["email"]
            except Exception as e:
                logger.warning(f"Failed to verify GitLab email via OIDC userinfo: {e}")

            if not external_id and user_info.get("id"):
                external_id = str(user_info["id"])
                username_hint = username_hint or user_info.get("username")
                if (
                    not email
                    and user_info.get("email")
                    and user_info.get("confirmed_at")
                ):
                    email = user_info["email"]

            if not email:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "GitLab account must have a verified email",
                        }
                    },
                )
        elif provider == "microsoft":
            external_id = user_info.get("id")
            username_hint = user_info.get("displayName")
            # SECURITY: Microsoft's /me endpoint returns mail/userPrincipalName
            # without any verification signal. Because oauth_login silently
            # links external identities to existing local accounts by verified
            # email, an attacker-controlled tenant could set a UPN equal to a
            # victim's address and take over their account. Require Microsoft's
            # OIDC email_verified claim before accepting the address.
            raw_email = user_info.get("mail") or user_info.get("userPrincipalName")
            if raw_email:
                try:
                    oidc_resp = await client.get(
                        "https://graph.microsoft.com/oidc/userinfo",
                        headers=user_info_headers,
                    )
                    email_verified = False
                    if oidc_resp.status_code == 200:
                        oidc_info = oidc_resp.json()
                        email_verified = bool(oidc_info.get("email_verified"))
                        if (
                            oidc_info.get("email")
                            and oidc_info["email"].lower() == raw_email.lower()
                            and email_verified
                        ):
                            email = raw_email
                    if not email:
                        logger.warning(
                            "Microsoft OAuth login presented unverified email; "
                            "refusing to link by address"
                        )
                except Exception as e:
                    logger.warning(f"Failed to verify Microsoft email claim: {e}")

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
                status_code=401,
                detail={"error": {"code": 401, "message": "Internal server error"}},
            )

        if result.status == AuthStatus.FAILED:
            error_code = 400 if "Age" in str(result.message) else 401
            raise HTTPException(
                status_code=error_code,
                detail={"error": {"code": error_code, "message": result.message}},
            )

        if result.status == AuthStatus.TWO_FACTOR_REQUIRED:
            return LoginResponse(
                # Enum-safe wire value (see login.py C1 hardening).
                status=AuthStatus.TWO_FACTOR_REQUIRED.value,
                token=None,
                user=None,
                challenge_token=result.challenge_token,
                methods=result.methods,
                expires_in=result.expires_in,
            )

        # Fail closed: never fall through to "success" with a null token.
        if result.status != AuthStatus.SUCCESS:
            logger.error(f"Unexpected auth status in OAuth callback: {result.status!r}")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

        logger.info(f"OAuth login successful for {provider}:{external_id}")

        # Consume the state cookie and hand out the HttpOnly session cookie.
        response.delete_cookie(
            OAUTH_STATE_COOKIE, path=_oauth_cookie_params(60)["path"]
        )
        # M9 (same as login): browsers receive the session ONLY via the
        # HttpOnly cookie; the body carries a marker plus the session
        # lifetime, with the raw token echoed back solely for explicit
        # non-browser opt-in (``X-Plexichat-Token-Mode: bearer``).
        if result.token:
            set_session_cookie(response, result.token)
        return LoginResponse(
            status=AuthStatus.SUCCESS.value,
            token=session_token_for_response(request, result.token),
            user=_user_to_response(result.user) if result.user else None,
            expires_in=session_cookie_max_age(),
        )
