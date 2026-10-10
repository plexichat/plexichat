"""
OAuth State Management - Server-side CSRF protection.

This module provides secure server-side storage for OAuth state tokens,
preventing CSRF attacks during the OAuth authorization flow.

Security model:
- State tokens are generated server-side with high entropy
- States are stored in database with short TTL (default 10 minutes)
- Each state can only be used once (marked as used after verification)
- States are bound to specific provider and redirect URI
- Optional nonce support for OpenID Connect providers
- Optional PKCE challenge storage for public clients

Configuration (in oauth section of config.yaml):
    state_ttl_seconds: 600       # How long states are valid (default: 10 minutes)
    state_token_bytes: 32        # Entropy for state tokens (default: 32 bytes)
    nonce_token_bytes: 32        # Entropy for OIDC nonce (default: 32 bytes)
    cleanup_on_verify: true      # Clean expired states on each verify (default: true)
    max_states_per_ip: 10        # Max pending states per IP (0 = unlimited)
"""

import secrets
import hashlib
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple

import utils.logger as logger
from src.core.base import timestamp_ms, generate_id


# Default configuration values
DEFAULT_STATE_TTL_SECONDS = 600  # 10 minutes
DEFAULT_STATE_TOKEN_BYTES = 32
DEFAULT_NONCE_TOKEN_BYTES = 32
DEFAULT_CLEANUP_ON_VERIFY = True
DEFAULT_MAX_STATES_PER_IP = 10


@dataclass
class OAuthState:
    """OAuth state record for CSRF protection."""

    id: int
    """Unique state record ID (snowflake)."""

    state_hash: str
    """SHA-256 hash of the state token."""

    provider: str
    """OAuth provider name (google, github, microsoft, etc.)."""

    redirect_uri: str
    """The redirect URI for this authorization request."""

    created_at: int
    """Unix timestamp (milliseconds) when state was created."""

    expires_at: int
    """Unix timestamp (milliseconds) when state expires."""

    used: bool = False
    """Whether this state has been consumed."""

    nonce: Optional[str] = None
    """Optional nonce for OpenID Connect (stored as hash)."""

    pkce_challenge: Optional[str] = None
    """Optional PKCE code_challenge for public clients."""

    ip_address: Optional[str] = None
    """IP address that initiated the OAuth flow (for audit)."""

    cookie_issued: bool = False
    """Whether a state-binding HttpOnly cookie was handed to the browser.

    When true, the callback MUST see the matching cookie or the flow is
    rejected (login-CSRF defense)."""

    # Not stored in DB, only set on creation
    state_token: Optional[str] = field(default=None, repr=False)
    """The actual state token (only available on creation)."""

    nonce_value: Optional[str] = field(default=None, repr=False)
    """The actual nonce value (only available on creation)."""


class OAuthStateManager:
    """
    Manages OAuth state tokens for CSRF protection.

    States are stored in the database with automatic expiration.
    Each state can only be used once to prevent replay attacks.

    Configuration options (passed in config dict):
        state_ttl_seconds: How long states are valid (default: 600)
        state_token_bytes: Entropy for state tokens (default: 32)
        nonce_token_bytes: Entropy for OIDC nonce (default: 32)
        cleanup_on_verify: Clean expired states on verify (default: true)
        max_states_per_ip: Max pending states per IP, 0=unlimited (default: 10)
    """

    def __init__(self, db, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the OAuth state manager.

        Args:
            db: Database instance.
            config: Optional OAuth configuration dict.
        """
        self._db = db
        self._config = config or {}
        self._ensure_table()
        logger.debug("OAuth state manager initialized")

    def _get_config(self, key: str, default: Any) -> Any:
        """Get configuration value with default."""
        return self._config.get(key, default)

    def _ensure_table(self) -> None:
        """Create the OAuth states table if it doesn't exist."""
        schema = """
        CREATE TABLE IF NOT EXISTS auth_oauth_states (
            id INTEGER PRIMARY KEY,
            state_hash TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used INTEGER DEFAULT 0,
            nonce_hash TEXT,
            pkce_challenge TEXT,
            ip_address TEXT,
            cookie_issued INTEGER DEFAULT 0
        )
        """
        # Convert for PostgreSQL if needed
        if hasattr(self._db, "convert_schema"):
            schema = self._db.convert_schema(schema)
        self._db.execute(schema)

        # Lazy column addition for databases created before cookie binding.
        try:
            self._db.fetch_one("SELECT cookie_issued FROM auth_oauth_states LIMIT 1")
        except Exception:
            try:
                col = (
                    self._db.convert_schema(
                        "ALTER TABLE auth_oauth_states ADD COLUMN cookie_issued INTEGER DEFAULT 0"
                    )
                    if hasattr(self._db, "convert_schema")
                    else "ALTER TABLE auth_oauth_states ADD COLUMN cookie_issued INTEGER DEFAULT 0"
                )
                self._db.execute(col)
            except Exception:
                pass  # Column added concurrently.

        # Index for cleanup queries
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_states_expires ON auth_oauth_states(expires_at)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_states_hash ON auth_oauth_states(state_hash)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_states_ip ON auth_oauth_states(ip_address)"
        )
        logger.debug("OAuth states table ready")

    def _generate_id(self) -> int:
        """Generate a snowflake ID."""
        return generate_id()

    def _current_time(self) -> int:
        """Get current Unix timestamp in milliseconds."""
        return timestamp_ms()

    def _hash_token(self, token: str) -> str:
        """Hash a token for storage using SHA-256."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def verify_nonce(
        self,
        stored_nonce_hash: Optional[str],
        presented_nonce: Optional[str],
    ) -> bool:
        """
        Verify a presented OIDC nonce against the stored hash (M6).

        The raw nonce is never persisted — only its SHA-256 hash is stored
        with the state record. Verification re-hashes the presented value
        and compares in constant time.

        Fail-closed semantics:
          - No nonce bound to this state (stored hash is None) -> True
            (caller skips the check; non-OIDC provider).
          - Nonce bound but presented value missing/empty -> False.
          - Mismatch -> False.

        Args:
            stored_nonce_hash: SHA-256 hex digest stored with the state.
            presented_nonce: Raw ``nonce`` claim from the IdP id_token.

        Returns:
            True only on match (or when no nonce was bound).
        """
        if not stored_nonce_hash:
            return True
        if not presented_nonce or not isinstance(presented_nonce, str):
            return False
        candidate_hash = self._hash_token(presented_nonce)
        try:
            return secrets.compare_digest(candidate_hash, stored_nonce_hash)
        except Exception:
            return False

    def _check_ip_rate_limit(self, ip_address: Optional[str]) -> bool:
        """
        Check if IP has too many pending states.

        Returns True if within limit, False if rate limited.
        """
        max_states = self._get_config("max_states_per_ip", DEFAULT_MAX_STATES_PER_IP)

        if max_states <= 0 or not ip_address:
            return True  # No limit or no IP

        now = self._current_time()
        row = self._db.fetch_one(
            """SELECT COUNT(*) as count FROM auth_oauth_states 
               WHERE ip_address = ? AND used = 0 AND expires_at > ?""",
            (ip_address, now),
        )

        count = row["count"] if row else 0
        if count >= max_states:
            logger.warning(
                f"OAuth state rate limit exceeded for IP {ip_address}: {count} pending states"
            )
            return False

        return True

    def create_state(
        self,
        provider: str,
        redirect_uri: str,
        include_nonce: bool = False,
        pkce_challenge: Optional[str] = None,
        ip_address: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        cookie_issued: bool = False,
    ) -> Optional[OAuthState]:
        """
        Create a new OAuth state for CSRF protection.

        Args:
            provider: OAuth provider name.
            redirect_uri: The redirect URI for this request.
            include_nonce: Whether to generate a nonce (for OIDC).
            pkce_challenge: Optional PKCE challenge for public clients.
            ip_address: Client IP address for audit.
            ttl_seconds: Custom TTL (default from config or 600s).
            cookie_issued: Whether a state-binding HttpOnly cookie accompanies
                this state; if so the callback requires it to match.

        Returns:
            OAuthState with state_token set (only available on creation),
            or None if rate limited.
        """
        # Check IP rate limit
        if not self._check_ip_rate_limit(ip_address):
            return None

        state_id = self._generate_id()
        now = self._current_time()

        # Get TTL from config or use default (10 minutes)
        if ttl_seconds is None:
            ttl_config = self._get_config(
                "state_ttl_seconds", DEFAULT_STATE_TTL_SECONDS
            )
            ttl_seconds = (
                int(ttl_config) if ttl_config is not None else DEFAULT_STATE_TTL_SECONDS
            )

        expires_at = now + (ttl_seconds * 1000)

        # Get token entropy from config
        state_token_bytes = self._get_config(
            "state_token_bytes", DEFAULT_STATE_TOKEN_BYTES
        )
        nonce_token_bytes = self._get_config(
            "nonce_token_bytes", DEFAULT_NONCE_TOKEN_BYTES
        )

        # Generate state token
        state_token = secrets.token_urlsafe(state_token_bytes)
        state_hash = self._hash_token(state_token)

        # Generate nonce if requested (for OIDC)
        nonce_value = None
        nonce_hash = None
        if include_nonce:
            nonce_value = secrets.token_urlsafe(nonce_token_bytes)
            nonce_hash = self._hash_token(nonce_value)

        # Store in database
        self._db.execute(
            """INSERT INTO auth_oauth_states
               (id, state_hash, provider, redirect_uri, created_at, expires_at,
                nonce_hash, pkce_challenge, ip_address, cookie_issued)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                state_id,
                state_hash,
                provider,
                redirect_uri,
                now,
                expires_at,
                nonce_hash,
                pkce_challenge,
                ip_address,
                1 if cookie_issued else 0,
            ),
        )

        logger.debug(
            f"Created OAuth state for {provider}, expires in {ttl_seconds}s, IP={ip_address}"
        )

        return OAuthState(
            id=state_id,
            state_hash=state_hash,
            provider=provider,
            redirect_uri=redirect_uri,
            created_at=now,
            expires_at=expires_at,
            used=False,
            nonce=nonce_hash,
            pkce_challenge=pkce_challenge,
            ip_address=ip_address,
            cookie_issued=cookie_issued,
            state_token=state_token,
            nonce_value=nonce_value,
        )

    def verify_state(
        self,
        state_token: str,
        provider: str,
        redirect_uri: str,
    ) -> Tuple[bool, Optional[OAuthState], Optional[str]]:
        """
        Verify and consume an OAuth state token.

        Args:
            state_token: The state token from the callback.
            provider: Expected OAuth provider.
            redirect_uri: Expected redirect URI.

        Returns:
            Tuple of (valid, state_record, error_message).
            If valid, state_record contains the verified state (with pkce_challenge if set).
            The state is marked as used and cannot be reused.
        """
        # Optionally cleanup expired states
        if self._get_config("cleanup_on_verify", DEFAULT_CLEANUP_ON_VERIFY):
            self.cleanup_expired()

        if not state_token:
            logger.debug("OAuth state verification failed: missing state token")
            return False, None, "Missing state token"

        state_hash = self._hash_token(state_token)
        now = self._current_time()

        # Look up state by hash
        row = self._db.fetch_one(
            """SELECT id, state_hash, provider, redirect_uri, created_at, expires_at,
                      used, nonce_hash, pkce_challenge, ip_address,
                      COALESCE(cookie_issued, 0) AS cookie_issued
               FROM auth_oauth_states WHERE state_hash = ?""",
            (state_hash,),
        )

        if not row:
            logger.warning("OAuth state verification failed: state not found")
            return False, None, "Invalid state token"

        # Check if already used (replay attack)
        if row["used"]:
            logger.warning(f"OAuth state replay attempt detected for state {row['id']}")
            return False, None, "State token already used"

        # Check expiration
        if row["expires_at"] < now:
            logger.warning(f"OAuth state expired for state {row['id']}")
            return False, None, "State token expired"

        # Verify provider matches
        if row["provider"] != provider:
            logger.warning(
                f"OAuth state provider mismatch: expected {row['provider']}, got {provider}"
            )
            return False, None, "State token provider mismatch"

        # Verify redirect URI matches
        if row["redirect_uri"] != redirect_uri:
            logger.warning(f"OAuth state redirect_uri mismatch for state {row['id']}")
            return False, None, "State token redirect URI mismatch"

        # Mark as used (atomic update)
        result = self._db.execute(
            "UPDATE auth_oauth_states SET used = 1 WHERE id = ? AND used = 0",
            (row["id"],),
        )

        # Check if we actually updated (handles race condition)
        if hasattr(result, "rowcount") and result.rowcount == 0:
            logger.warning(f"OAuth state race condition for state {row['id']}")
            return False, None, "State token already used"

        logger.debug(f"OAuth state verified and consumed for {provider}")

        state = OAuthState(
            id=row["id"],
            state_hash=row["state_hash"],
            provider=row["provider"],
            redirect_uri=row["redirect_uri"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            used=True,
            nonce=row["nonce_hash"],
            pkce_challenge=row["pkce_challenge"],
            ip_address=row["ip_address"],
            cookie_issued=bool(row.get("cookie_issued", 0)),
        )

        return True, state, None

    def cleanup_expired(self) -> int:
        """
        Remove expired OAuth states from the database.

        Returns:
            Number of states removed.
        """
        now = self._current_time()
        result = self._db.execute(
            "DELETE FROM auth_oauth_states WHERE expires_at < ?", (now,)
        )

        count = result.rowcount if hasattr(result, "rowcount") else 0
        if count > 0:
            logger.debug(f"Cleaned up {count} expired OAuth states")

        return count


# Module-level singleton for convenience
_state_manager: Optional[OAuthStateManager] = None


def setup(db, config: Optional[Dict[str, Any]] = None) -> OAuthStateManager:
    """
    Initialize the OAuth state manager.

    Args:
        db: Database instance.
        config: Optional OAuth configuration.

    Returns:
        Configured OAuthStateManager instance.
    """
    global _state_manager
    _state_manager = OAuthStateManager(db, config)
    logger.info("OAuth state manager setup complete")
    return _state_manager


def get_manager() -> Optional[OAuthStateManager]:
    """Get the OAuth state manager instance."""
    return _state_manager


def create_oauth_state(
    provider: str,
    redirect_uri: str,
    include_nonce: bool = False,
    pkce_challenge: Optional[str] = None,
    ip_address: Optional[str] = None,
    cookie_issued: bool = False,
) -> Optional[OAuthState]:
    """
    Create a new OAuth state (convenience function).

    Args:
        provider: OAuth provider name.
        redirect_uri: The redirect URI.
        include_nonce: Whether to include a nonce.
        pkce_challenge: Optional PKCE challenge.
        ip_address: Client IP address.
        cookie_issued: Whether the caller set a state-binding cookie.

    Returns:
        OAuthState or None if manager not initialized or rate limited.
    """
    if _state_manager is None:
        logger.error("OAuth state manager not initialized")
        return None
    return _state_manager.create_state(
        provider=provider,
        redirect_uri=redirect_uri,
        include_nonce=include_nonce,
        pkce_challenge=pkce_challenge,
        ip_address=ip_address,
        cookie_issued=cookie_issued,
    )


def verify_oauth_state(
    state_token: str,
    provider: str,
    redirect_uri: str,
) -> Tuple[bool, Optional[OAuthState], Optional[str]]:
    """
    Verify an OAuth state (convenience function).

    Args:
        state_token: The state token from callback.
        provider: Expected provider.
        redirect_uri: Expected redirect URI.

    Returns:
        Tuple of (valid, state, error_message).
    """
    if _state_manager is None:
        logger.error("OAuth state manager not initialized")
        return False, None, "OAuth state manager not initialized"
    return _state_manager.verify_state(state_token, provider, redirect_uri)


def cleanup_expired_states() -> int:
    """
    Clean up expired OAuth states (convenience function).

    Returns:
        Number of states removed.
    """
    if _state_manager is None:
        return 0
    return _state_manager.cleanup_expired()


def verify_oauth_nonce(
    stored_nonce_hash: Optional[str],
    presented_nonce: Optional[str],
) -> bool:
    """
    Verify a presented OIDC nonce against a stored hash (M6).

    Constant-time comparison via :meth:`OAuthStateManager.verify_nonce`;
    fails closed when a nonce is bound but the presented value is
    missing or mismatched. Returns True when no nonce was bound
    (non-OIDC provider) so callers can skip the check.

    Falls back to a direct constant-time string comparison when no
    manager is initialised (same fail-closed contract).
    """
    if not stored_nonce_hash:
        return True
    if not presented_nonce or not isinstance(presented_nonce, str):
        return False
    if _state_manager is not None:
        return _state_manager.verify_nonce(stored_nonce_hash, presented_nonce)
    try:
        candidate = hashlib.sha256(presented_nonce.encode("utf-8")).hexdigest()
        return secrets.compare_digest(candidate, stored_nonce_hash)
    except Exception:
        return False
