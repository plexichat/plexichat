"""
OAuth2 flows - Authorization code grant and bot auth flow.
"""

import time
import json
from typing import Optional, List, Dict, Any
from urllib.parse import urlencode

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from ..models import OAuth2Token, OAuth2AuthorizationCode
from ..exceptions import (
    InvalidClientError,
    InvalidGrantError,
    InvalidScopeError,
    InvalidRedirectUriError,
    AuthorizationCodeExpiredError,
    TokenExpiredError,
    TokenRevokedError,
)
from .scopes import validate_scopes
from .tokens import (
    generate_authorization_code,
    generate_access_token,
    generate_refresh_token,
    verify_token_hash,
    parse_oauth_token,
)


class OAuth2Flow:
    """Handles OAuth2 authorization flows."""

    def __init__(self, db, config: Dict[str, Any]):
        """
        Initialize OAuth2 flow handler.

        Args:
            db: Database instance
            config: OAuth configuration
        """
        self._db = db
        self._config = config

    def _current_time(self) -> int:
        """Get current Unix timestamp."""
        return int(time.time() * 1000)

    def generate_authorization_url(
        self,
        application_id: int,
        redirect_uri: str,
        scopes: List[str],
        state: Optional[str] = None,
        permissions: Optional[str] = None,
    ) -> str:
        """
        Generate an OAuth2 authorization URL.

        Args:
            application_id: Application ID
            redirect_uri: Redirect URI
            scopes: List of scopes
            state: Optional state parameter
            permissions: Optional bot permissions

        Returns:
            Authorization URL
        """
        base_url = self._config.get("authorization_endpoint", "/oauth2/authorize")

        params = {
            "client_id": str(application_id),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
        }

        if state:
            params["state"] = state

        if permissions and "bot" in scopes:
            params["permissions"] = permissions

        return f"{base_url}?{urlencode(params)}"

    def validate_authorization_request(
        self,
        application_id: int,
        redirect_uri: str,
        scopes: List[str],
    ) -> Dict[str, Any]:
        """
        Validate an authorization request.

        Args:
            application_id: Application ID
            redirect_uri: Redirect URI
            scopes: List of scopes

        Returns:
            Dict with application info

        Raises:
            InvalidClientError: Invalid application
            InvalidRedirectUriError: Invalid redirect URI
            InvalidScopeError: Invalid scopes
        """
        app = self._db.fetch_one(
            "SELECT id, name, redirect_uris, icon_url FROM app_applications WHERE id = ?",
            (application_id,),
        )

        if not app:
            raise InvalidClientError("Application not found")

        allowed_uris = json.loads(app["redirect_uris"])
        if redirect_uri not in allowed_uris:
            raise InvalidRedirectUriError(
                f"Redirect URI not registered: {redirect_uri}"
            )

        valid, issues = validate_scopes(scopes)
        if not valid:
            raise InvalidScopeError(", ".join(issues))

        return {
            "application_id": app["id"],
            "application_name": app["name"],
            "application_icon": app["icon_url"],
            "redirect_uri": redirect_uri,
            "scopes": scopes,
        }

    def create_authorization_code(
        self,
        application_id: int,
        user_id: int,
        redirect_uri: str,
        scopes: List[str],
    ) -> OAuth2AuthorizationCode:
        """
        Create an authorization code after user consent.

        Args:
            application_id: Application ID
            user_id: User ID
            redirect_uri: Redirect URI
            scopes: Granted scopes

        Returns:
            OAuth2AuthorizationCode
        """
        code_id = generate_snowflake_id()
        now = self._current_time()
        expires_at = now + self._config.get("code_expiry_seconds", 600)

        full_code, code_hash = generate_authorization_code(code_id)

        self._db.execute(
            """INSERT INTO app_oauth_codes
               (id, application_id, user_id, code_hash, redirect_uri, scopes, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                code_id,
                application_id,
                user_id,
                code_hash,
                redirect_uri,
                json.dumps(scopes),
                expires_at,
                now,
            ),
        )

        logger.debug(
            f"Authorization code created for app {application_id}, user {user_id}"
        )

        return OAuth2AuthorizationCode(
            id=code_id,
            application_id=application_id,
            user_id=user_id,
            code_hash=code_hash,
            redirect_uri=redirect_uri,
            scopes=scopes,
            expires_at=expires_at,
            created_at=now,
            code=full_code,
        )

    def exchange_code(
        self,
        application_id: int,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> OAuth2Token:
        """
        Exchange an authorization code for tokens.

        Args:
            application_id: Application ID
            client_secret: Client secret
            code: Authorization code
            redirect_uri: Redirect URI (must match original)

        Returns:
            OAuth2Token with access and refresh tokens

        Raises:
            InvalidClientError: Invalid client credentials
            InvalidGrantError: Invalid or expired code
        """
        app = self._db.fetch_one(
            "SELECT id, client_secret_hash FROM app_applications WHERE id = ?",
            (application_id,),
        )

        if not app:
            raise InvalidClientError("Application not found")

        if not verify_token_hash(client_secret, app["client_secret_hash"]):
            raise InvalidClientError("Invalid client secret")

        parsed = parse_oauth_token(code)
        if not parsed or parsed["token_type"] != "auth":
            raise InvalidGrantError("Invalid authorization code format")

        code_record = self._db.fetch_one(
            """SELECT id, application_id, user_id, code_hash, redirect_uri, scopes,
                      expires_at, used
               FROM app_oauth_codes WHERE id = ?""",
            (parsed["id"],),
        )

        if not code_record:
            raise InvalidGrantError("Authorization code not found")

        if code_record["application_id"] != application_id:
            raise InvalidGrantError("Code was not issued to this application")

        if code_record["used"]:
            raise InvalidGrantError("Authorization code already used")

        if code_record["expires_at"] < self._current_time():
            raise AuthorizationCodeExpiredError()

        if not verify_token_hash(parsed["secret"], code_record["code_hash"]):
            raise InvalidGrantError("Invalid authorization code")

        if code_record["redirect_uri"] != redirect_uri:
            raise InvalidGrantError("Redirect URI mismatch")

        self._db.execute(
            "UPDATE app_oauth_codes SET used = 1 WHERE id = ?", (parsed["id"],)
        )

        scopes = json.loads(code_record["scopes"])
        token = self._create_token(
            application_id,
            code_record["user_id"],
            scopes,
        )

        logger.info(
            f"Code exchanged for tokens: app {application_id}, user {code_record['user_id']}"
        )

        return token

    def refresh_token(
        self,
        application_id: int,
        client_secret: str,
        refresh_token: str,
    ) -> OAuth2Token:
        """
        Refresh an access token.

        Args:
            application_id: Application ID
            client_secret: Client secret
            refresh_token: Refresh token

        Returns:
            New OAuth2Token

        Raises:
            InvalidClientError: Invalid client credentials
            InvalidGrantError: Invalid refresh token
        """
        if not self._config.get("refresh_enabled", True):
            raise InvalidGrantError("Token refresh is disabled")

        app = self._db.fetch_one(
            "SELECT id, client_secret_hash FROM app_applications WHERE id = ?",
            (application_id,),
        )

        if not app:
            raise InvalidClientError("Application not found")

        if not verify_token_hash(client_secret, app["client_secret_hash"]):
            raise InvalidClientError("Invalid client secret")

        parsed = parse_oauth_token(refresh_token)
        if not parsed or parsed["token_type"] != "refresh":
            raise InvalidGrantError("Invalid refresh token format")

        token_record = self._db.fetch_one(
            """SELECT id, application_id, user_id, refresh_token_hash, scopes, revoked
               FROM app_oauth_tokens WHERE id = ?""",
            (parsed["id"],),
        )

        if not token_record:
            raise InvalidGrantError("Refresh token not found")

        if token_record["application_id"] != application_id:
            raise InvalidGrantError("Token was not issued to this application")

        if token_record["revoked"]:
            raise TokenRevokedError()

        if not token_record["refresh_token_hash"]:
            raise InvalidGrantError("No refresh token associated")

        if not verify_token_hash(parsed["secret"], token_record["refresh_token_hash"]):
            raise InvalidGrantError("Invalid refresh token")

        self._db.execute(
            "UPDATE app_oauth_tokens SET revoked = 1 WHERE id = ?", (parsed["id"],)
        )

        scopes = json.loads(token_record["scopes"])
        new_token = self._create_token(
            application_id,
            token_record["user_id"],
            scopes,
        )

        logger.info(
            f"Token refreshed: app {application_id}, user {token_record['user_id']}"
        )

        return new_token

    def verify_access_token(self, access_token: str) -> Dict[str, Any]:
        """
        Verify an access token.

        Args:
            access_token: Access token

        Returns:
            Dict with token info

        Raises:
            InvalidGrantError: Invalid token
            TokenExpiredError: Token expired
            TokenRevokedError: Token revoked
        """
        parsed = parse_oauth_token(access_token)
        if not parsed or parsed["token_type"] != "access":
            raise InvalidGrantError("Invalid access token format")

        token_record = self._db.fetch_one(
            """SELECT id, application_id, user_id, access_token_hash, scopes,
                      expires_at, revoked
               FROM app_oauth_tokens WHERE id = ?""",
            (parsed["id"],),
        )

        if not token_record:
            raise InvalidGrantError("Access token not found")

        if token_record["revoked"]:
            raise TokenRevokedError()

        if token_record["expires_at"] < self._current_time():
            raise TokenExpiredError()

        if not verify_token_hash(parsed["secret"], token_record["access_token_hash"]):
            raise InvalidGrantError("Invalid access token")

        return {
            "token_id": token_record["id"],
            "application_id": token_record["application_id"],
            "user_id": token_record["user_id"],
            "scopes": json.loads(token_record["scopes"]),
            "expires_at": token_record["expires_at"],
        }

    def revoke_token(self, token: str) -> bool:
        """
        Revoke an access or refresh token.

        Args:
            token: Token to revoke

        Returns:
            True if revoked
        """
        parsed = parse_oauth_token(token)
        if not parsed:
            return False

        if parsed["token_type"] not in ("access", "refresh"):
            return False

        self._db.execute(
            "UPDATE app_oauth_tokens SET revoked = 1 WHERE id = ?", (parsed["id"],)
        )

        logger.debug(f"Token revoked: {parsed['id']}")
        return True

    def revoke_user_tokens(self, application_id: int, user_id: int) -> int:
        """
        Revoke all tokens for a user on an application.

        Args:
            application_id: Application ID
            user_id: User ID

        Returns:
            Number of tokens revoked
        """
        result = self._db.execute(
            """UPDATE app_oauth_tokens SET revoked = 1
               WHERE application_id = ? AND user_id = ? AND revoked = 0""",
            (application_id, user_id),
        )

        count = result.rowcount if hasattr(result, "rowcount") else 0
        logger.debug(f"Revoked {count} tokens for app {application_id}, user {user_id}")
        return count

    def _create_token(
        self,
        application_id: int,
        user_id: int,
        scopes: List[str],
    ) -> OAuth2Token:
        """Create a new OAuth2 token pair."""
        token_id = generate_snowflake_id()
        now = self._current_time()
        expires_at = now + self._config.get("token_expiry_seconds", 604800)

        access_token, access_hash = generate_access_token(token_id)

        refresh_token = None
        refresh_hash = None
        if self._config.get("refresh_enabled", True):
            refresh_token, refresh_hash = generate_refresh_token(token_id)

        self._db.execute(
            """INSERT INTO app_oauth_tokens
               (id, application_id, user_id, access_token_hash, refresh_token_hash,
                scopes, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                application_id,
                user_id,
                access_hash,
                refresh_hash,
                json.dumps(scopes),
                expires_at,
                now,
            ),
        )

        return OAuth2Token(
            id=token_id,
            application_id=application_id,
            user_id=user_id,
            access_token_hash=access_hash,
            refresh_token_hash=refresh_hash,
            scopes=scopes,
            expires_at=expires_at,
            created_at=now,
            access_token=access_token,
            refresh_token=refresh_token,
        )
