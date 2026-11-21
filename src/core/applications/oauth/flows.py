"""
OAuth2 flows - Authorization code grant and bot auth flow.
"""

import time
import json
from typing import Optional, List, Dict, Any
from urllib.parse import urlencode

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from src.core.security.hardening import HardeningViolation

from ..models import OAuth2Token, OAuth2AuthorizationCode
from ..validation import require_exact_redirect_match, require_oauth_redirect_url
from ..exceptions import (
    InvalidClientError,
    InvalidGrantError,
    InvalidScopeError,
    InvalidRedirectUriError,
    InvalidPKCEError,
    AuthorizationCodeExpiredError,
    TokenExpiredError,
    TokenRevokedError,
)
from .scopes import (
    VALID_SCOPES,
    require_allowed_scopes,
    require_valid_scopes,
    scopes_are_subset,
    validate_permission_string,
)
from .tokens import (
    PKCE_METHOD_S256,
    generate_authorization_code,
    generate_access_token,
    generate_refresh_token,
    validate_client_secret,
    validate_token_string,
    validate_pkce_challenge,
    validate_pkce_method,
    validate_pkce_verifier,
    verify_pkce_challenge,
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

    @staticmethod
    def _require_scopes(scopes: List[str]) -> List[str]:
        """Validate scopes, translating the core error into the OAuth error."""
        try:
            return require_valid_scopes(scopes)
        except HardeningViolation as exc:
            raise InvalidScopeError(str(exc)) from exc

    def _application_allowed_scopes(self, application_id: int) -> List[str]:
        """Read the application's scope ceiling.

        Legacy rows (and databases predating the column) store an empty or
        absent ceiling; those are treated as the full default set so existing
        applications keep working. A malformed explicit ceiling fails closed.
        """
        row = self._db.fetch_one(
            "SELECT allowed_scopes FROM app_applications WHERE id = ?",
            (application_id,),
        )
        if not row:
            raise InvalidClientError("Application not found")
        if isinstance(row, dict):
            raw = row.get("allowed_scopes")
        else:
            try:
                raw = row["allowed_scopes"]
            except (KeyError, IndexError):
                raw = None
        if isinstance(raw, str) and raw:
            try:
                entries = json.loads(raw)
            except (ValueError, TypeError):
                raise InvalidScopeError("Application scope configuration is invalid")
            if not isinstance(entries, list):
                raise InvalidScopeError("Application scope configuration is invalid")
            if not entries:
                # Legacy marker: no explicit ceiling recorded.
                return sorted(VALID_SCOPES)
            try:
                return require_allowed_scopes(entries)
            except HardeningViolation as exc:
                raise InvalidScopeError(str(exc)) from exc
        return sorted(VALID_SCOPES)

    def _require_scopes_for_app(
        self, application_id: int, scopes: List[str]
    ) -> List[str]:
        """Reject any scope the application is not configured to grant."""
        allowed = self._application_allowed_scopes(application_id)
        if not scopes_are_subset(scopes, allowed):
            denied = sorted(set(scopes) - set(allowed))
            raise InvalidScopeError(
                "Application is not permitted to grant: " + ", ".join(denied)
            )
        return scopes

    @staticmethod
    def _require_client_secret(secret: str) -> str:
        try:
            return validate_client_secret(secret)
        except HardeningViolation as exc:
            raise InvalidClientError(str(exc)) from exc

    @staticmethod
    def _require_token(token: str, field: str = "token") -> str:
        try:
            return validate_token_string(token, field=field)
        except HardeningViolation as exc:
            raise InvalidGrantError(str(exc)) from exc

    @staticmethod
    def _require_permissions(permissions: Optional[str]) -> Optional[str]:
        """Validate an optional bot permission bitfield."""
        if permissions is None:
            return None
        try:
            return validate_permission_string(permissions)
        except HardeningViolation as exc:
            raise InvalidScopeError(str(exc)) from exc

    def _pkce_required(self) -> bool:
        """Whether deployments mandate PKCE for every authorization code."""
        return bool(self._config.get("pkce_enabled", False))

    @staticmethod
    def _require_pkce_challenge(challenge: Optional[str]) -> str:
        if challenge is None:
            raise InvalidPKCEError("PKCE code_challenge is required")
        try:
            return validate_pkce_challenge(challenge)
        except HardeningViolation as exc:
            raise InvalidPKCEError(str(exc)) from exc

    @staticmethod
    def _require_pkce_method(method: Optional[str]) -> str:
        try:
            return validate_pkce_method(method)
        except HardeningViolation as exc:
            raise InvalidPKCEError(str(exc)) from exc

    @staticmethod
    def _require_pkce_verifier(verifier: Optional[str]) -> str:
        if verifier is None:
            raise InvalidGrantError("PKCE code_verifier is required")
        try:
            return validate_pkce_verifier(verifier)
        except HardeningViolation as exc:
            raise InvalidGrantError(str(exc)) from exc

    def _bind_pkce(
        self,
        code_challenge: Optional[str],
        code_challenge_method: Optional[str],
    ) -> Optional[str]:
        """Validate PKCE parameters and return the challenge to persist.

        PKCE is enforced whenever the deployment requires it or the client
        supplied a challenge. A challenge, once bound to a code, is never
        optional at exchange time (no downgrade path).
        """
        if not self._pkce_required() and code_challenge is None:
            return None
        self._require_pkce_method(code_challenge_method)
        return self._require_pkce_challenge(code_challenge)

    def generate_authorization_url(
        self,
        application_id: int,
        redirect_uri: str,
        scopes: List[str],
        state: Optional[str] = None,
        permissions: Optional[str] = None,
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
    ) -> str:
        """
        Generate an OAuth2 authorization URL.

        Args:
            application_id: Application ID
            redirect_uri: Redirect URI
            scopes: List of scopes
            state: Optional state parameter
            permissions: Optional bot permissions
            code_challenge: RFC 7636 S256 challenge (required when PKCE is on)
            code_challenge_method: Must be ``S256`` when a challenge is used

        Returns:
            Authorization URL
        """
        scopes = self._require_scopes_for_app(
            application_id, self._require_scopes(scopes)
        )
        permissions = self._require_permissions(permissions)
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

        bound_challenge = self._bind_pkce(code_challenge, code_challenge_method)
        if bound_challenge:
            params["code_challenge"] = bound_challenge
            params["code_challenge_method"] = PKCE_METHOD_S256

        return f"{base_url}?{urlencode(params)}"

    def validate_authorization_request(
        self,
        application_id: int,
        redirect_uri: str,
        scopes: List[str],
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
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
        try:
            redirect_uri = require_oauth_redirect_url(redirect_uri)
        except HardeningViolation as exc:
            raise InvalidRedirectUriError(str(exc)) from exc

        app = self._db.fetch_one(
            "SELECT id, name, redirect_uris, icon_url FROM app_applications WHERE id = ?",
            (application_id,),
        )

        if not app:
            raise InvalidClientError("Application not found")

        allowed_uris = json.loads(app["redirect_uris"])
        try:
            redirect_uri = require_exact_redirect_match(redirect_uri, allowed_uris)
        except HardeningViolation as exc:
            raise InvalidRedirectUriError(str(exc)) from exc

        scopes = self._require_scopes_for_app(
            application_id, self._require_scopes(scopes)
        )
        bound_challenge = self._bind_pkce(code_challenge, code_challenge_method)

        return {
            "application_id": app["id"],
            "application_name": app["name"],
            "application_icon": app["icon_url"],
            "redirect_uri": redirect_uri,
            "scopes": scopes,
            "code_challenge": bound_challenge,
        }

    def create_authorization_code(
        self,
        application_id: int,
        user_id: int,
        redirect_uri: str,
        scopes: List[str],
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
    ) -> OAuth2AuthorizationCode:
        """
        Create an authorization code after user consent.

        Args:
            application_id: Application ID
            user_id: User ID
            redirect_uri: Redirect URI
            scopes: Granted scopes
            code_challenge: RFC 7636 S256 challenge bound to this code
            code_challenge_method: Must be ``S256`` when a challenge is used

        Returns:
            OAuth2AuthorizationCode
        """
        try:
            redirect_uri = require_oauth_redirect_url(redirect_uri)
        except HardeningViolation as exc:
            raise InvalidRedirectUriError(str(exc)) from exc
        # Shape validation is not enough: a code is the credential that carries
        # a user's grant to the callback, so it must be bound to a URI the app
        # owner actually registered. ``validate_authorization_request`` enforces
        # this on the authorize path, but code creation is a separate entry
        # point — a caller reaching it with an attacker-supplied, well-formed
        # callback could otherwise mint a code that turns the client into an
        # open redirector and enables code interception. Re-check the exact
        # allow-list here so every issuance path enforces the same rule.
        # Apps with no registered callbacks (legacy/unconfigured rows) keep the
        # old behaviour; a configured allow-list is always authoritative.
        app_row = self._db.fetch_one(
            "SELECT redirect_uris FROM app_applications WHERE id = ?",
            (application_id,),
        )
        if not app_row:
            raise InvalidClientError("Application not found")
        try:
            allowed_uris = json.loads(app_row["redirect_uris"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise InvalidRedirectUriError(
                "Application redirect URI configuration is invalid"
            ) from exc
        if allowed_uris:
            try:
                redirect_uri = require_exact_redirect_match(redirect_uri, allowed_uris)
            except HardeningViolation as exc:
                raise InvalidRedirectUriError(str(exc)) from exc
        scopes = self._require_scopes_for_app(
            application_id, self._require_scopes(scopes)
        )
        bound_challenge = self._bind_pkce(code_challenge, code_challenge_method)
        bound_method = PKCE_METHOD_S256 if bound_challenge else None

        code_id = generate_snowflake_id()
        now = self._current_time()
        expires_at = now + int(self._config.get("code_expiry_seconds", 600)) * 1000

        full_code, code_hash = generate_authorization_code(code_id)

        self._db.execute(
            """INSERT INTO app_oauth_codes
               (id, application_id, user_id, code_hash, redirect_uri, scopes,
                expires_at, created_at, code_challenge, code_challenge_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                code_id,
                application_id,
                user_id,
                code_hash,
                redirect_uri,
                json.dumps(scopes),
                expires_at,
                now,
                bound_challenge,
                bound_method,
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
            code_challenge=bound_challenge,
            code_challenge_method=bound_method,
        )

    def exchange_code(
        self,
        application_id: int,
        client_secret: str,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> OAuth2Token:
        """
        Exchange an authorization code for tokens.

        Args:
            application_id: Application ID
            client_secret: Client secret
            code: Authorization code
            redirect_uri: Redirect URI (must match original)
            code_verifier: RFC 7636 verifier, mandatory for PKCE-bound codes

        Returns:
            OAuth2Token with access and refresh tokens

        Raises:
            InvalidClientError: Invalid client credentials
            InvalidGrantError: Invalid, expired or PKCE-mismatched code
        """
        client_secret = self._require_client_secret(client_secret)
        code = self._require_token(code, "authorization code")
        try:
            redirect_uri = require_oauth_redirect_url(redirect_uri)
        except HardeningViolation as exc:
            raise InvalidGrantError(str(exc)) from exc

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
                      expires_at, used, code_challenge, code_challenge_method
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

        # RFC 7636: a code bound to a challenge cannot be redeemed without the
        # matching verifier. The verifier is never stored; we re-derive the
        # S256 challenge and compare in constant time. There is deliberately
        # no downgrade path — if a challenge exists, the verifier is mandatory.
        stored_challenge = code_record["code_challenge"]
        # No legacy non-PKCE fallback: a deployment that requires PKCE must
        # never redeem a code that carries no challenge (e.g. a row issued
        # before the PKCE migration or through a path that bypassed
        # ``_bind_pkce``). Accepting such a code would let anyone holding the
        # authorization code skip PKCE entirely - a silent downgrade of the
        # strict enforcement added in the PKCE wave.
        if self._pkce_required() and not stored_challenge:
            raise InvalidPKCEError(
                "PKCE code_challenge is required for this authorization code"
            )
        if stored_challenge:
            presented = self._require_pkce_verifier(code_verifier)
            if not verify_pkce_challenge(presented, stored_challenge):
                raise InvalidGrantError("PKCE verification failed")

        cursor = self._db.execute(
            "UPDATE app_oauth_codes SET used = 1 WHERE id = ? AND used = 0",
            (parsed["id"],),
        )
        # SECURITY: fail closed. If the driver does not expose rowcount we
        # cannot prove the single-use guard fired, so the exchange MUST
        # fail rather than risk double-spending the code.
        rowcount = getattr(cursor, "rowcount", None)
        if rowcount is None:
            raise InvalidGrantError(
                "Authorization code could not be atomically consumed"
            )
        if rowcount == 0:
            raise InvalidGrantError("Authorization code already used")

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

        client_secret = self._require_client_secret(client_secret)
        refresh_token = self._require_token(refresh_token, "refresh token")

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

        # SECURITY: consume the refresh token atomically. The ``revoked``
        # check above is only a fast-fail — two concurrent refreshes can both
        # read ``revoked = 0`` before either writes. The conditional UPDATE
        # plus rowcount guard (mirroring authorization-code exchange) makes
        # the refresh single-use: exactly one caller can flip revoked from 0
        # to 1, and everyone else fails. Fail closed when the driver does not
        # expose rowcount so the single-use guarantee can never be bypassed.
        cursor = self._db.execute(
            "UPDATE app_oauth_tokens SET revoked = 1 WHERE id = ? AND revoked = 0",
            (parsed["id"],),
        )
        rowcount = getattr(cursor, "rowcount", None)
        if rowcount is None:
            raise InvalidGrantError("Refresh token could not be atomically consumed")
        if rowcount == 0:
            raise InvalidGrantError("Refresh token already used")

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
        access_token = self._require_token(access_token, "access token")
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

        # SECURITY: verify the token secret hash BEFORE checking expiry.
        # This avoids a timing side-channel where a non-holder can
        # distinguish valid-but-expired tokens from invalid tokens by
        # measuring response time.
        if not verify_token_hash(parsed["secret"], token_record["access_token_hash"]):
            raise InvalidGrantError("Invalid access token")

        if token_record["expires_at"] < self._current_time():
            raise TokenExpiredError()

        try:
            self._db.execute(
                "UPDATE app_oauth_tokens SET last_used_at = ? WHERE id = ?",
                (self._current_time(), token_record["id"]),
            )
        except Exception as exc:
            logger.error(
                f"Failed to update last_used_at for OAuth token "
                f"{token_record['id']}: {exc}"
            )

        return {
            "token_id": token_record["id"],
            "application_id": token_record["application_id"],
            "user_id": token_record["user_id"],
            "scopes": json.loads(token_record["scopes"]),
            "expires_at": token_record["expires_at"],
        }

    def revoke_token(self, token: str, user_id: Optional[int] = None) -> bool:
        """
        Revoke an access or refresh token.

        SECURITY: the caller must present the real token secret (verified
        in constant time against the stored hash) and, when ``user_id`` is
        supplied, must own the token record. Unknown IDs return False.

        Args:
            token: Token to revoke
            user_id: Optional owner that must match the token's user

        Returns:
            True if revoked
        """
        parsed = parse_oauth_token(token)
        if not parsed:
            return False

        if parsed["token_type"] not in ("access", "refresh"):
            return False

        token_record = self._db.fetch_one(
            """SELECT id, application_id, user_id, access_token_hash,
                      refresh_token_hash, revoked
               FROM app_oauth_tokens WHERE id = ?""",
            (parsed["id"],),
        )

        # Fail closed: a nonexistent ID is never a silent success.
        if not token_record:
            return False

        hash_column = (
            "access_token_hash"
            if parsed["token_type"] == "access"
            else "refresh_token_hash"
        )
        expected_hash = token_record[hash_column]
        if not expected_hash or not verify_token_hash(parsed["secret"], expected_hash):
            logger.warning(
                f"Token revocation denied for {parsed['id']}: secret mismatch"
            )
            return False

        if user_id is not None and token_record["user_id"] != user_id:
            logger.warning(
                f"Token revocation denied for {parsed['id']}: "
                f"user {user_id} does not own the token"
            )
            return False

        self._db.execute(
            "UPDATE app_oauth_tokens SET revoked = 1 WHERE id = ?",
            (parsed["id"],),
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
        # Last line of defence: no token may ever carry a scope the owning
        # application is not permitted to grant, even if a caller reached this
        # helper with a hand-crafted scope list.
        scopes = self._require_scopes_for_app(
            application_id, self._require_scopes(scopes)
        )
        token_id = generate_snowflake_id()
        now = self._current_time()
        expires_at = now + int(self._config.get("token_expiry_seconds", 604800)) * 1000

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
