"""
Authentication routes - Register, login, logout endpoints.
"""

import os
import sys
import httpx
from urllib.parse import urlencode
from typing import List
from fastapi import APIRouter, Request, HTTPException, Depends

import src.api as api
import utils.logger as logger
from utils.logger import mask_email, mask_string
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    LoginResponse,
    OAuthLoginResponse,
    OAuthCallbackRequest,
    TwoFactorRequest,
    UserResponse,
    SessionResponse,
    TwoFactorStatusResponse,
    TwoFactorSetupRequest,
    TwoFactorSetupResponse,
    TwoFactorConfirmRequest,
    TwoFactorDisableRequest,
    RevokeAllSessionsRequest,
    RevokeAllSessionsResponse,
    PasswordRequirementsResponse,
    PasswordResetRequest,
    PasswordResetConfirm,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse

# Import typed auth exceptions for proper error handling
from src.core.auth.exceptions import (
    AuthError,
    InvalidCredentialsError,
    AccountLockedError,
    AccountDisabledError,
    EmailNotVerifiedError,
    TokenExpiredError,
    TokenInvalidError,
    TwoFactorRequiredError,
    TwoFactorInvalidError,
    UserExistsError,
    UserNotFoundError,
    WeakPasswordError,
    InvalidUsernameError,
    InvalidEmailError,
    TwoFactorSetupError,
)

# Import OAuth security module
from src.core.auth.oauth import (
    create_oauth_state,
    verify_oauth_state,
    generate_pkce_pair,
    verify_pkce,
)

# Import config utility
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
for path in [project_root, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    import utils.config as config_util
except ImportError:
    config_util = None

router = APIRouter(tags=["Authentication"])


def _user_to_response(user) -> UserResponse:
    """Convert user object to response model."""
    try:
        return UserResponse(
            id=SnowflakeID(user.id),
            username=user.username,
            email=getattr(user, "email", None),
            avatar_url=getattr(user, "avatar_url", None),
            created_at=user.created_at,
            email_verified=getattr(user, "email_verified", False),
            totp_enabled=getattr(user, "totp_enabled", False),
            age_verified=getattr(user, "age_verified", False),
        )
    except Exception as e:
        logger.error(f"Error converting user object to response: {e}")
        raise e


@router.post(
    "/register",
    response_model=LoginResponse,
    summary="Register a new user",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid registration data"},
        401: {
            "model": ErrorResponse,
            "description": "Invalid credentials or session expired",
        },
        409: {"model": ErrorResponse, "description": "User already exists"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def register(request: Request, body: RegisterRequest) -> LoginResponse:
    """
    Register a new user account.

    Creates a new user with the provided credentials and returns a session token.
    If alpha registration mode is enabled, automatically grants alpha tier and badge.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    ip_address = request.client.host if request.client else None

    # Handle age_verified boolean as an alternative to explicit age
    age = body.age
    if age is None and body.age_verified is True:
        # Get minimum age from config or default to 13
        min_age = 13
        if config_util:
            min_age = config_util.get("authentication.accounts.minimum_age", 13)
        age = min_age

    try:
        user = auth.register(
            username=body.username,
            email=body.email,
            password=body.password,
            ip_address=ip_address,
            age=age,
            dob=body.dob,
        )
    except UserExistsError:
        masked_username = mask_string(body.username)
        masked_email_addr = mask_email(body.email)
        logger.warning(
            f"Registration failed: User '{masked_username}' or email '{masked_email_addr}' already exists"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": 409,
                    "message": "Username or email already exists",
                }
            },
        )
    except (InvalidUsernameError, InvalidEmailError, WeakPasswordError, AuthError) as e:
        # AuthError could be age-related
        masked_username = mask_string(body.username)
        logger.warning(f"Registration failed for '{masked_username}': {e}")
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )
    except Exception as e:
        masked_username = mask_string(body.username)
        logger.error(
            f"Unexpected error in register for '{masked_username}': {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Internal server error during registration"}}
        )

    # Apply alpha tester features if enabled
    features = api.get_features()
    if features:
        try:
            features.apply_new_user_features(user.id)
        except Exception as fe:
            logger.debug(
                f"Failed to apply new user features for user {user.id}: {fe}"
            )

    try:
        result = auth.login(
            username=body.username, password=body.password, ip_address=ip_address
        )
    except Exception as le:
        logger.error(
            f"Auto-login failed after registration for user {user.id}: {le}"
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": 401,
                    "message": "Auto-login failed after registration",
                }
            },
        )

    return LoginResponse(
        status="success", token=result.token, user=_user_to_response(user)
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="User login",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials"},
        403: {
            "model": ErrorResponse,
            "description": "Account locked or email not verified",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def login(request: Request, body: LoginRequest) -> LoginResponse:
    """
    Authenticate a user.

    Returns a session token on success, or a 2FA challenge if enabled.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")

    try:
        result = auth.login(
            username=body.username,
            password=body.password,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except InvalidCredentialsError:
        masked_username = mask_string(body.username)
        logger.warning(
            f"Login failed for '{masked_username}': Invalid credentials"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid credentials"}},
        )
    except AccountLockedError:
        masked_username = mask_string(body.username)
        logger.warning(f"Login failed for '{masked_username}': Account locked")
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": "Account locked"}}
        )
    except (EmailNotVerifiedError, AccountDisabledError) as e:
        masked_username = mask_string(body.username)
        logger.warning(
            f"Login failed for '{masked_username}': {type(e).__name__}"
        )
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )
    except Exception as e:
        masked_username = mask_string(body.username)
        logger.error(
            f"Unexpected error in login for '{masked_username}': {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Internal server error during login"}}
        )

    if result.status.value == "two_factor_required":
        masked_username = mask_string(body.username)
        logger.info(f"2FA challenge issued for user '{masked_username}'")
        return LoginResponse(
            status="two_factor_required",
            token=None,
            user=None,
            challenge_token=result.challenge_token,
            methods=result.methods,
            expires_in=result.expires_in,
        )

    masked_username = mask_string(body.username)
    logger.info(
        f"User '{masked_username}' logged in successfully (ID: {getattr(result.user, 'id', 'unknown')})"
    )
    return LoginResponse(
        status="success",
        token=result.token,
        user=_user_to_response(result.user) if result.user else None,
        challenge_token=None,
        methods=None,
        expires_in=None,
    )


# OAuth Providers Configuration
# Each provider has auth/token/userinfo URLs and required scopes
# PKCE support is indicated per provider (GitHub doesn't support it)
OAUTH_PROVIDERS = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "user_info_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        "scopes": ["openid", "email", "profile"],
        "supports_pkce": True,
        "supports_nonce": True,  # OpenID Connect
    },
    "github": {
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "user_info_url": "https://api.github.com/user",
        "scopes": ["read:user", "user:email"],
        "supports_pkce": False,  # GitHub doesn't support PKCE
        "supports_nonce": False,
    },
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "user_info_url": "https://graph.microsoft.com/v1.0/me",
        "scopes": ["openid", "email", "profile", "User.Read"],
        "supports_pkce": True,
        "supports_nonce": True,  # OpenID Connect
    },
    "gitlab": {
        "auth_url": "https://gitlab.com/oauth/authorize",
        "token_url": "https://gitlab.com/oauth/token",
        "user_info_url": "https://gitlab.com/api/v4/user",
        "scopes": ["read_user", "openid", "profile", "email"],
        "supports_pkce": True,
        "supports_nonce": True,
    },
}


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
    """
    Initiate an OAuth login flow with secure server-side state management.

    Security features:
    - Server-side state storage (CSRF protection)
    - PKCE support for providers that support it
    - Nonce for OpenID Connect providers
    - Rate limiting per IP address

    Returns the authorization URL to redirect the user to.
    """
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

    # Get config for provider
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
    ip_address = request.client.host if request.client else None

    # Check if PKCE is enabled in config (default: True for supported providers)
    pkce_enabled = oauth_config.get("pkce_enabled", True) and provider_info.get(
        "supports_pkce", False
    )

    # Generate PKCE pair if enabled, using config for parameters
    pkce_challenge = None
    code_verifier = None
    if pkce_enabled:
        pkce_config = oauth_config.get("pkce", {})
        pkce = generate_pkce_pair(config=pkce_config)
        pkce_challenge = pkce.code_challenge
        code_verifier = pkce.code_verifier

    # Create server-side state with optional nonce for OIDC providers
    include_nonce = provider_info.get("supports_nonce", False)
    oauth_state = create_oauth_state(
        provider=provider,
        redirect_uri=redirect_uri,
        include_nonce=include_nonce,
        pkce_challenge=pkce_challenge,
        ip_address=ip_address,
    )

    if not oauth_state:
        # Could be rate limited or state manager not initialized
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

    # Build authorization URL parameters
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider_info["scopes"]),
        "state": oauth_state.state_token,
    }

    # Add PKCE parameters if enabled
    if pkce_enabled and pkce_challenge:
        params["code_challenge"] = pkce_challenge
        params["code_challenge_method"] = "S256"

    # Add nonce for OIDC providers
    if include_nonce and oauth_state.nonce_value:
        params["nonce"] = oauth_state.nonce_value

    # Provider-specific parameters
    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "select_account"

    # URL-encode parameters properly
    query_string = urlencode(params)
    auth_url = f"{provider_info['auth_url']}?{query_string}"

    logger.info(f"OAuth login initiated for {provider} from {ip_address}")

    # Return state token and code_verifier (client needs verifier for token exchange)
    response = OAuthLoginResponse(url=auth_url, state=oauth_state.state_token or "")

    # If PKCE is enabled, we need to return the code_verifier to the client
    # The client must store this and send it back during callback
    # We store the challenge server-side, client keeps the verifier
    if code_verifier:
        # Add code_verifier to response - client must include this in callback
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
    """
    Handle OAuth callback and complete login with secure state verification.

    Security features:
    - Server-side state verification (CSRF protection)
    - PKCE verification if enabled
    - Single-use state tokens (replay protection)
    """
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

    # Verify state server-side (CSRF protection)
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

    # Verify PKCE if it was used
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
        # Get PKCE config for verification
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

    # Get config for provider
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
        # 1. Exchange code for access token
        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": callback_data.code,
            "grant_type": "authorization_code",
            "redirect_uri": callback_data.redirect_uri,
        }

        # Include PKCE verifier in token exchange if used
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

        # 2. Get user info
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

        # 3. Process user info based on provider
        external_id = None
        email = None
        username_hint = None

        if provider == "google":
            external_id = user_info.get("sub")
            email = user_info.get("email")
            username_hint = user_info.get("name")
        elif provider == "github":
            external_id = str(user_info.get("id"))
            email = user_info.get("email")
            username_hint = user_info.get("login")
            # GitHub might not return email in main user info if private
            if not email:
                try:
                    emails_resp = await client.get(
                        "https://api.github.com/user/emails", headers=user_info_headers
                    )
                    if emails_resp.status_code == 200:
                        emails = emails_resp.json()
                        primary_email = next(
                            (e["email"] for e in emails if e["primary"]),
                            emails[0]["email"] if emails else None,
                        )
                        email = primary_email
                except Exception:
                    pass
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

        # 4. Perform OAuth login via AuthManager
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
            # Handle specific failure cases like age verification
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


@router.post(
    "/2fa",
    response_model=LoginResponse,
    summary="Complete 2FA",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired 2FA code"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def complete_2fa(body: TwoFactorRequest) -> LoginResponse:
    """
    Complete two-factor authentication.

    Validates the 2FA code and returns a session token on success.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        result = auth.complete_2fa(body.challenge_token, body.code)
    except (TokenInvalidError, TokenExpiredError) as e:
        logger.warning(f"2FA completion failed: {e}")
        raise HTTPException(
            status_code=401, detail={"error": {"code": 401, "message": str(e)}}
        )
    except TwoFactorInvalidError:
        logger.warning("2FA completion failed: Invalid code")
        raise HTTPException(
            status_code=401, detail={"error": {"code": 401, "message": "Invalid 2FA code"}}
        )
    except UserNotFoundError:
        logger.warning("2FA completion failed: User not found")
        raise HTTPException(
            status_code=401, detail={"error": {"code": 401, "message": "Invalid challenge"}}
        )
    except Exception as e:
        logger.error(f"Unexpected error in complete_2fa: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )

    logger.info(
        f"2FA completed successfully for user ID: {getattr(result.user, 'id', 'unknown')}"
    )
    return LoginResponse(
        status="success",
        token=result.token,
        user=_user_to_response(result.user) if result.user else None,
        challenge_token=None,
        methods=None,
        expires_in=None,
    )


@router.post(
    "/logout",
    response_model=SuccessResponse,
    summary="Logout session",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def logout(
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Logout current session.

    Revokes the current session token.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    if current_user.session_id:
        try:
            auth.revoke_session(current_user.user_id, current_user.session_id)
            logger.info(
                f"User {current_user.user_id} logged out session {current_user.session_id}"
            )
        except Exception as e:
            logger.debug(
                f"Failed to revoke session {current_user.session_id} during logout: {e}"
            )

    return SuccessResponse(success=True)


@router.post(
    "/refresh",
    response_model=LoginResponse,
    summary="Refresh session token",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired session"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def refresh_session(request: Request) -> LoginResponse:
    """
    Refresh the current session.

    Validates the current session token and returns a potentially updated token.
    If the session is near expiration, it will be extended.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    # Get token from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Missing authentication token"}},
        )

    token = auth_header[7:]
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")

    try:
        # verify_token will handle extension if enabled
        token_info = auth.verify_token(token, ip_address, user_agent)

        # Get user object for response
        user = auth.get_user(token_info.user_id)
        if not user:
            raise UserNotFoundError("User not found")

        return LoginResponse(
            status="success",
            token=token,  # For now, we return the same token as it's just been extended in DB
            user=_user_to_response(user),
        )
    except (TokenInvalidError, TokenExpiredError) as e:
        logger.warning(f"Session refresh failed: {e}")
        raise HTTPException(
            status_code=401, detail={"error": {"code": 401, "message": str(e)}}
        )
    except Exception as e:
        logger.error(f"Unexpected error in refresh_session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/2fa/status",
    response_model=TwoFactorStatusResponse,
    summary="Get 2FA status",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_2fa_status(
    current_user: TokenInfo = Depends(get_current_user),
) -> TwoFactorStatusResponse:
    """
    Get current 2FA status for the user.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        status = auth.get_2fa_status(current_user.user_id)
        return TwoFactorStatusResponse(
            enabled=status.enabled, backup_codes_remaining=status.backup_codes_remaining
        )
    except UserNotFoundError:
        logger.warning(
            f"2FA status check failed: User {current_user.user_id} not found"
        )
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "User not found"}},
        )
    except Exception as e:
        logger.error(
            f"Failed to get 2FA status for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/sessions",
    response_model=List[SessionResponse],
    summary="List active sessions",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_sessions_list(
    current_user: TokenInfo = Depends(get_current_user),
) -> List[SessionResponse]:
    """
    Get all active sessions for the current user.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        sessions = auth.get_sessions(current_user.user_id)
        return [
            SessionResponse(
                id=SnowflakeID(s.id),
                device_id=getattr(s, "device_id", None),
                ip_address=getattr(s, "ip_address", None),
                user_agent=getattr(s, "user_agent", None),
                created_at=getattr(s, "created_at", 0),
                expires_at=getattr(s, "expires_at", 0),
                last_activity=getattr(s, "last_activity", 0),
                current=s.id == current_user.session_id,
            )
            for s in sessions
        ]
    except Exception as e:
        logger.error(
            f"Failed to list sessions for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/sessions/{session_id}",
    response_model=SuccessResponse,
    summary="Revoke session",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid session ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Session not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_session(
    session_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Revoke a specific session.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        sid = int(session_id)
    except ValueError:
        logger.warning(f"Invalid session ID format: {session_id}")
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid session ID"}},
        )

    try:
        auth.revoke_session(current_user.user_id, sid)
        logger.info(f"User {current_user.user_id} revoked session {sid}")
        return SuccessResponse(success=True)
    except UserNotFoundError:
        logger.warning(
            f"Session {sid} not found for user {current_user.user_id}"
        )
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Session not found"}},
        )
    except Exception as e:
        logger.error(
            f"Failed to revoke session {session_id} for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/2fa/enable",
    response_model=TwoFactorSetupResponse,
    summary="Enable 2FA",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Password required or invalid data",
        },
        401: {"model": ErrorResponse, "description": "Invalid password"},
        404: {"model": ErrorResponse, "description": "User not found"},
        409: {"model": ErrorResponse, "description": "2FA is already enabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def enable_2fa(
    body: TwoFactorSetupRequest, current_user: TokenInfo = Depends(get_current_user)
) -> TwoFactorSetupResponse:
    """
    Enable 2FA - returns QR code and secret.

    Requires password confirmation. Returns setup data for authenticator app.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    password = body.password
    if not password:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Password required"}},
        )

    try:
        user = auth.get_user(current_user.user_id)
        if not user:
            logger.warning(f"User {current_user.user_id} not found during 2FA enable")
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        # Check if already enabled
        if getattr(user, "totp_enabled", False):
            logger.warning(
                f"User {current_user.user_id} attempted to enable 2FA but it is already enabled"
            )
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": 409, "message": "2FA is already enabled"}},
            )

        # Verify password by attempting a login (this validates credentials)
        try:
            auth.login(user.username, password)
        except InvalidCredentialsError:
            logger.warning(
                f"2FA enable failed for user {current_user.user_id}: Invalid password"
            )
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Invalid password"}},
            )
        except TwoFactorRequiredError:
            # Password was correct, 2FA just required - this is fine
            pass
        except (AccountLockedError, AccountDisabledError) as e:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": str(e)}},
            )

        # Setup 2FA - returns TwoFactorSetup object
        result = auth.setup_2fa(current_user.user_id)
        logger.info(f"2FA setup initiated for user {current_user.user_id}")
        return TwoFactorSetupResponse(
            secret=result.secret,
            qr_uri=result.qr_uri,
            backup_codes=result.backup_codes or [],
        )
    except HTTPException:
        raise
    except AuthError as e:
        # Handle any remaining auth errors
        if "already" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": 409, "message": "2FA is already enabled"}},
            )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(
            f"Failed to enable 2FA for user {current_user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/2fa/confirm",
    response_model=SuccessResponse,
    summary="Confirm 2FA setup",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Valid 6-digit code required or 2FA setup not started",
        },
        401: {"model": ErrorResponse, "description": "Invalid code"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def confirm_2fa_setup(
    body: TwoFactorConfirmRequest, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Confirm 2FA setup with TOTP code.

    Validates the code from authenticator app to complete 2FA setup.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    code = body.code
    if not code or len(code) != 6:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Valid 6-digit code required"}},
        )

    try:
        success = auth.confirm_2fa(current_user.user_id, code)
    except TwoFactorInvalidError:
        logger.warning(
            f"2FA confirm failed for user {current_user.user_id}: Invalid code"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid code"}},
        )
    except (UserNotFoundError, TwoFactorSetupError):
        logger.warning(
            f"2FA confirm failed for user {current_user.user_id}: Setup not started"
        )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "2FA setup not started"}},
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in confirm_2fa_setup for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )

    if success:
        logger.info(f"2FA confirmed and enabled for user {current_user.user_id}")
        return SuccessResponse(success=True)
    else:
        logger.warning(
            f"2FA confirm failed for user {current_user.user_id}: Invalid code"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid code"}},
        )


@router.post(
    "/2fa/disable",
    response_model=SuccessResponse,
    summary="Disable 2FA",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Password or code required or 2FA not enabled",
        },
        401: {"model": ErrorResponse, "description": "Invalid password or code"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def disable_2fa(
    body: TwoFactorDisableRequest, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Disable 2FA.

    Requires password and current 2FA code for security.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    password = body.password
    code = body.code

    if not password:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Password required"}},
        )
    if not code:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "2FA code required"}},
        )

    try:
        auth.disable_2fa(current_user.user_id, password, code)
        logger.info(f"2FA disabled for user {current_user.user_id}")
        return SuccessResponse(success=True)
    except InvalidCredentialsError:
        logger.warning(
            f"2FA disable failed for user {current_user.user_id}: Invalid password"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid password"}},
        )
    except TwoFactorInvalidError:
        logger.warning(
            f"2FA disable failed for user {current_user.user_id}: Invalid 2FA code"
        )
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid 2FA code"}},
        )
    except AuthError as e:
        if "not enabled" in str(e).lower():
            logger.warning(
                f"2FA disable failed for user {current_user.user_id}: 2FA not enabled"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "2FA is not enabled"}},
            )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in disable_2fa for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/sessions/revoke-all",
    response_model=RevokeAllSessionsResponse,
    summary="Revoke all sessions",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_all_sessions(
    body: RevokeAllSessionsRequest, current_user: TokenInfo = Depends(get_current_user)
) -> RevokeAllSessionsResponse:
    """
    Revoke all sessions except optionally the current one.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    except_current = body.except_current

    try:
        sessions = auth.get_sessions(current_user.user_id)
        revoked = 0
        for s in sessions:
            if except_current and s.id == current_user.session_id:
                continue
            try:
                auth.revoke_session(current_user.user_id, s.id)
                revoked += 1
            except Exception as e:
                logger.debug(
                    f"Failed to revoke session {s.id} for user {current_user.user_id}: {e}"
                )

        logger.info(
            f"User {current_user.user_id} revoked {revoked} sessions (except_current={except_current})"
        )
        return RevokeAllSessionsResponse(success=True, revoked_count=revoked)
    except Exception as e:
        logger.error(
            f"Failed to revoke all sessions for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/password-requirements",
    response_model=PasswordRequirementsResponse,
    summary="Get password requirements",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_password_requirements() -> PasswordRequirementsResponse:
    """
    Get server password requirements.

    Returns the password policy configuration so clients can validate
    passwords before submission and display requirements to users.
    """
    try:
        # Get authentication config directly from config utility to avoid duplication
        auth_config = {}
        if config_util:
            auth_config = config_util.get("authentication", {})
            if not isinstance(auth_config, dict):
                auth_config = {}
        
        password_config = auth_config.get("password", {})
        if not isinstance(password_config, dict):
            password_config = {}
            
        accounts_config = auth_config.get("accounts", {})
        if not isinstance(accounts_config, dict):
            accounts_config = {}

        from src.api.routes.docs import is_docs_enabled

        # Default values matching main.py
        return PasswordRequirementsResponse(
            min_length=password_config.get("min_length", 12),
            max_length=password_config.get("max_length", 128),
            require_uppercase=password_config.get("require_uppercase", True),
            require_lowercase=password_config.get("require_lowercase", True),
            require_digit=password_config.get("require_digit", True),
            require_special=password_config.get("require_special", True),
            age_gate_enabled=accounts_config.get("age_gate_enabled", False),
            age_verification_type=accounts_config.get("age_verification_type", "boolean"),
            minimum_age=accounts_config.get("minimum_age", 13),
            docs_enabled=is_docs_enabled(),
        )
    except Exception as e:
        logger.error(f"Failed to get password requirements: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/password-reset/request",
    response_model=SuccessResponse,
    summary="Request password reset",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def request_password_reset(body: PasswordResetRequest) -> SuccessResponse:
    """
    Request a password reset email.

    Always returns success to prevent email enumeration.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        # Use run_in_threadpool if auth methods are blocking
        from fastapi.concurrency import run_in_threadpool
        
        def _request_reset_with_cleanup(email_str):
            db = api.get_db()
            try:
                return auth.request_password_reset(email_str)
            finally:
                if db:
                    db.close()

        await run_in_threadpool(_request_reset_with_cleanup, body.email)
        return SuccessResponse(success=True)
    except Exception as e:
        logger.error(f"Password reset request failed: {e}", exc_info=True)
        # Still return success to prevent email enumeration
        return SuccessResponse(success=True)


@router.post(
    "/password-reset/confirm",
    response_model=SuccessResponse,
    summary="Confirm password reset",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid token or weak password"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def confirm_password_reset(body: PasswordResetConfirm) -> SuccessResponse:
    """
    Confirm password reset with token.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        from fastapi.concurrency import run_in_threadpool
        
        def _reset_with_cleanup(token_str, new_password):
            db = api.get_db()
            try:
                return auth.reset_password(token_str, new_password)
            finally:
                if db:
                    db.close()

        success = await run_in_threadpool(_reset_with_cleanup, body.token, body.new_password)
        if success:
            return SuccessResponse(success=True)
        else:
            raise TokenInvalidError("Invalid or expired token")
    except TokenInvalidError as e:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": str(e)}},
        )
    except WeakPasswordError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"Password reset confirmation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
