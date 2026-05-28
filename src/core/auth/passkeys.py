"""
WebAuthn/FIDO2 Passkey authentication module.

Provides secure passkey registration and authentication using the
webauthn library.

Security features:
- Challenge-based registration and authentication
- Signature counter verification (anti-cloning)
- RP ID validation
- Attestation verification (optional)
- Backup credential detection
"""

import base64
import dataclasses
import json
import os
import secrets
import time
import utils.config as config
import utils.logger as logger
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.utils.encryption import EncryptionManager


class _WebAuthnJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles webauthn-specific types like bytes."""

    def default(self, o: Any) -> Any:
        if isinstance(o, bytes):
            return base64.urlsafe_b64encode(o).rstrip(b"=").decode("ascii")
        return super().default(o)


# WebAuthn components (defined as Any to satisfy pyright)
generate_authentication_options: Any = None
generate_registration_options: Any = None
verify_authentication_response: Any = None
verify_registration_response: Any = None
AttestationConveyancePreference: Any = None
AuthenticatorSelectionCriteria: Any = None
PublicKeyCredentialDescriptor: Any = None
ResidentKeyRequirement: Any = None
UserVerificationRequirement: Any = None

# WebAuthn imports
try:
    from webauthn import (  # type: ignore
        generate_authentication_options as _gao,
        generate_registration_options as _gro,
        verify_authentication_response as _vao,
        verify_registration_response as _vro,
    )
    from webauthn.helpers.structs import (  # type: ignore
        AttestationConveyancePreference as _acp,
        AuthenticatorSelectionCriteria as _asc,
        PublicKeyCredentialDescriptor as _pkd,
        ResidentKeyRequirement as _rkr,
        UserVerificationRequirement as _uvr,
    )

    generate_authentication_options = _gao
    generate_registration_options = _gro
    verify_authentication_response = _vao
    verify_registration_response = _vro
    AttestationConveyancePreference = _acp
    AuthenticatorSelectionCriteria = _asc
    PublicKeyCredentialDescriptor = _pkd
    ResidentKeyRequirement = _rkr
    UserVerificationRequirement = _uvr
    WEBAUTHN_AVAILABLE = True
except ImportError:
    WEBAUTHN_AVAILABLE = False
    logger.warning("webauthn library not installed. Passkey support disabled.")


@dataclass
class PasskeyCredential:
    """Stored passkey credential."""

    id: int
    user_id: int
    credential_id: str
    credential_public_key: bytes
    sign_count: int
    device_type: Optional[str]
    device_name: Optional[str]
    aaguid: Optional[str]
    transports: List[str]
    backed_up: bool
    created_at: int
    last_used_at: Optional[int]
    revoked: bool


@dataclass
class RegistrationOptions:
    """WebAuthn registration options for client."""

    challenge_id: str
    rp_id: str
    rp_name: str
    user_id: str  # WebAuthn user handle
    user_name: str
    challenge: bytes
    options_dict: Dict[str, Any]


@dataclass
class AuthenticationOptions:
    """WebAuthn authentication options for client."""

    challenge_id: str
    rp_id: str
    challenge: bytes
    options_dict: Dict[str, Any]


class PasskeyManager:
    """Manages WebAuthn/FIDO2 passkey operations."""

    def __init__(self, db):
        """Initialize the passkey manager.

        Args:
            db: Database instance
        """
        self._db = db
        encryption_cfg = config.get("encryption", {}).get("argon2", {})
        self._crypto = EncryptionManager(
            argon2_time_cost=encryption_cfg.get("time_cost", 2),
            argon2_memory_cost=encryption_cfg.get("memory_cost", 65536),
            argon2_parallelism=encryption_cfg.get("parallelism", 2),
        )
        self._config = config.get("authentication", {}).get("passkeys", {})
        self._challenge_ttl = self._config.get("challenge_ttl_seconds", 300)

        # Check if passkeys are enabled
        if not self._config.get("enabled", True):
            logger.info("Passkeys are disabled in configuration")

        # Get RP configuration with validation
        self._rp_name = self._config.get("rp_name", "Plexichat")
        self._rp_id = self._config.get("rp_id")
        if not self._rp_id:
            logger.warning(
                "PASSKEY_RP_ID not configured. Passkeys will not work correctly. "
                "Set authentication.passkeys.rp_id to your domain (e.g., 'api.plexichat.com')"
            )
            self._rp_id = "localhost"

        # Validate and get origin
        self._origin = self._config.get("origin")
        if not self._origin:
            logger.warning(
                "PASSKEY_ORIGIN not configured. Passkeys may not work correctly. "
                "Set authentication.passkeys.origin to your full origin (e.g., 'https://api.plexichat.com')"
            )
            self._origin = f"http://{self._rp_id}"

        # Validate origin is a valid URL
        if not self._origin.startswith(("http://", "https://")):
            logger.error(
                f"Invalid passkey origin '{self._origin}'. Must start with http:// or https://"
            )
            self._origin = f"http://{self._rp_id}"

        if not WEBAUTHN_AVAILABLE:
            logger.error(
                "PasskeyManager initialized but webauthn library not available. "
                "Install with: pip install webauthn==2.5.0"
            )

    def _get_timestamp(self) -> int:
        """Get current Unix timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a snowflake ID."""
        from src.utils.encryption import generate_snowflake_id

        return generate_snowflake_id()

    def _base64url_encode(self, data: bytes) -> str:
        """Base64url encode bytes without padding."""
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def _base64url_decode(self, data: str) -> bytes:
        """Base64url decode string with padding restoration."""
        padding = 4 - len(data) % 4
        if padding != 4:
            data += "=" * padding
        return base64.urlsafe_b64decode(data)

    def is_available(self) -> bool:
        """Check if passkey support is available."""
        return WEBAUTHN_AVAILABLE

    def _ensure_tables(self) -> None:
        """Ensure passkey tables exist."""
        if not WEBAUTHN_AVAILABLE:
            return

        # Passkey credentials table
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_passkeys (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                credential_id TEXT NOT NULL,
                credential_public_key BLOB NOT NULL,
                sign_count INTEGER DEFAULT 0,
                device_type TEXT,
                device_name TEXT,
                aaguid TEXT,
                transports TEXT,
                backed_up INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                last_used_at INTEGER,
                revoked INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
                UNIQUE(credential_id)
            )
            """
        )

        # Passkey challenges table (temporary storage)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_passkey_challenges (
                id INTEGER PRIMARY KEY,
                challenge_id TEXT UNIQUE NOT NULL,
                user_id INTEGER,
                challenge_type TEXT NOT NULL,
                challenge BLOB NOT NULL,
                device_name TEXT,
                expires_at INTEGER NOT NULL,
                used INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )

        # Indexes
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_passkeys_user ON auth_passkeys(user_id)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_passkeys_credential ON auth_passkeys(credential_id)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_passkey_challenges_expires ON auth_passkey_challenges(expires_at)"
        )

        # Add passkey_enabled column to auth_users if not exists
        try:
            self._db.execute(
                "ALTER TABLE auth_users ADD COLUMN passkey_enabled INTEGER DEFAULT 0"
            )
        except Exception:  # nosec B110
            pass  # Column may already exist - intentional for migrations

        try:
            self._db.execute(
                "ALTER TABLE auth_users ADD COLUMN webauthn_user_handle TEXT"
            )
        except Exception:  # nosec B110
            pass  # Column may already exist - intentional for migrations

    def generate_registration_options(
        self,
        user_id: int,
        username: str,
        device_name: Optional[str] = None,
    ) -> Optional[RegistrationOptions]:
        """Generate WebAuthn registration options for a user.

        Args:
            user_id: Internal user ID
            username: User's username
            device_name: Optional device name hint

        Returns:
            RegistrationOptions with challenge and client options
        """
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("webauthn library not installed")

        self._ensure_tables()

        # Generate or retrieve WebAuthn user handle
        user_row = self._db.fetch_one(
            "SELECT webauthn_user_handle FROM auth_users WHERE id = ?", (user_id,)
        )
        webauthn_user_handle = user_row["webauthn_user_handle"] if user_row else None
        if not webauthn_user_handle:
            webauthn_user_handle = self._base64url_encode(os.urandom(32))
            self._db.execute(
                "UPDATE auth_users SET webauthn_user_handle = ? WHERE id = ?",
                (webauthn_user_handle, user_id),
            )

        # Get existing credentials for exclusion
        existing_creds = self._db.fetch_all(
            "SELECT credential_id FROM auth_passkeys WHERE user_id = ? AND revoked = 0",
            (user_id,),
        )
        exclude_credentials = []
        for row in existing_creds:
            cred_id = row["credential_id"]
            exclude_credentials.append(
                PublicKeyCredentialDescriptor(id=self._base64url_decode(cred_id))
            )

        # Generate challenge
        challenge = os.urandom(32)
        challenge_id = secrets.token_urlsafe(16)
        expires_at = self._get_timestamp() + (self._challenge_ttl * 1000)

        # Store challenge
        self._db.execute(
            """
            INSERT INTO auth_passkey_challenges
            (id, challenge_id, user_id, challenge_type, challenge, device_name, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._generate_id(),
                challenge_id,
                user_id,
                "registration",
                challenge,
                device_name,
                expires_at,
                self._get_timestamp(),
            ),
        )

        # Generate WebAuthn options
        options = generate_registration_options(
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_id=webauthn_user_handle.encode(),
            user_name=username,
            user_display_name=username,
            challenge=challenge,
            exclude_credentials=exclude_credentials,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            attestation=AttestationConveyancePreference.DIRECT,
        )

        # Convert to dict for JSON serialization with proper bytes handling
        options_dict = json.loads(
            json.dumps(dataclasses.asdict(options), cls=_WebAuthnJSONEncoder)
        )

        return RegistrationOptions(
            challenge_id=challenge_id,
            rp_id=self._rp_id,
            rp_name=self._rp_name,
            user_id=webauthn_user_handle,
            user_name=username,
            challenge=challenge,
            options_dict=options_dict,
        )

    def verify_registration(
        self,
        user_id: int,
        challenge_id: str,
        credential_response: Dict[str, Any],
    ) -> Optional[PasskeyCredential]:
        """Complete passkey registration.

        Args:
            user_id: Internal user ID
            challenge_id: Challenge ID from registration options
            credential_response: Client credential response (JSON)

        Returns:
            Stored PasskeyCredential
        """
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("webauthn library not installed")

        # Verify and consume challenge
        challenge_row = self._db.fetch_one(
            """
            SELECT * FROM auth_passkey_challenges
            WHERE challenge_id = ? AND challenge_type = ? AND used = 0
            """,
            (challenge_id, "registration"),
        )
        if not challenge_row:
            raise ValueError("Invalid or expired challenge")

        if challenge_row["expires_at"] < self._get_timestamp():
            raise ValueError("Challenge expired")

        if challenge_row["user_id"] != user_id:
            raise ValueError("Challenge user mismatch")

        # Mark challenge as used with atomic check to prevent replay
        result = self._db.execute(
            "UPDATE auth_passkey_challenges SET used = 1 WHERE id = ? AND used = 0",
            (challenge_row["id"],),
        )
        if result.rowcount == 0:
            raise ValueError("Challenge already used - possible replay attack")

        challenge = challenge_row["challenge"]
        device_name = challenge_row.get("device_name")

        # Verify the registration response
        try:
            verification = verify_registration_response(
                credential=credential_response,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
            )
        except Exception as e:
            logger.error(f"Passkey registration verification failed: {e}")
            raise ValueError(f"Registration verification failed: {e}")

        # Extract credential info
        credential_id = self._base64url_encode(verification.credential_id)
        credential_public_key = verification.credential_public_key
        sign_count = verification.sign_count

        # Extract attestation info if available
        device_type = None
        aaguid = None
        transports = []
        backed_up = False

        if "response" in credential_response:
            response = credential_response["response"]
            if "transports" in response:
                transports = response["transports"]

        if verification.aaguid:
            aaguid = str(verification.aaguid)

        if verification.credential_device_type:
            device_type = verification.credential_device_type.value

        if verification.credential_backed_up is not None:
            backed_up = verification.credential_backed_up

        # Store credential
        now = self._get_timestamp()
        passkey_id = self._generate_id()

        self._db.execute(
            """
            INSERT INTO auth_passkeys
            (id, user_id, credential_id, credential_public_key, sign_count,
             device_type, device_name, aaguid, transports, backed_up, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                passkey_id,
                user_id,
                credential_id,
                credential_public_key,
                sign_count,
                device_type,
                device_name or "Unknown Device",
                aaguid,
                ",".join(transports),
                1 if backed_up else 0,
                now,
            ),
        )

        # Enable passkey flag on user
        self._db.execute(
            "UPDATE auth_users SET passkey_enabled = 1 WHERE id = ?",
            (user_id,),
        )

        logger.info(f"Passkey registered for user {user_id}: {credential_id[:20]}...")

        return PasskeyCredential(
            id=passkey_id,
            user_id=user_id,
            credential_id=credential_id,
            credential_public_key=credential_public_key,
            sign_count=sign_count,
            device_type=device_type,
            device_name=device_name or "Unknown Device",
            aaguid=aaguid,
            transports=transports,
            backed_up=backed_up,
            created_at=now,
            last_used_at=None,
            revoked=False,
        )

    def generate_authentication_options(
        self,
        username: Optional[str] = None,
    ) -> AuthenticationOptions:
        """Generate authentication options for passkey login.

        Args:
            username: Optional username to filter credentials

        Returns:
            AuthenticationOptions with challenge
        """
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("webauthn library not installed")

        self._ensure_tables()

        # Generate challenge
        challenge = os.urandom(32)
        challenge_id = secrets.token_urlsafe(16)
        expires_at = self._get_timestamp() + (self._challenge_ttl * 1000)

        # Build allow_credentials list
        allow_credentials = []
        if username:
            # Look up user and their credentials
            email_index = self._crypto.blind_index(username, "user_email")
            user_row = self._db.fetch_one(
                "SELECT id, webauthn_user_handle FROM auth_users WHERE username = ? OR email_index = ?",
                (username, email_index),
            )
            if user_row:
                user_id = user_row["id"]
                creds = self._db.fetch_all(
                    "SELECT credential_id FROM auth_passkeys WHERE user_id = ? AND revoked = 0",
                    (user_id,),
                )
                for row in creds:
                    cred_id = row["credential_id"]
                    allow_credentials.append(
                        PublicKeyCredentialDescriptor(
                            id=self._base64url_decode(cred_id)
                        )
                    )

        # Store challenge (user_id is NULL for now - will be resolved after auth)
        self._db.execute(
            """
            INSERT INTO auth_passkey_challenges
            (id, challenge_id, user_id, challenge_type, challenge, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._generate_id(),
                challenge_id,
                None,  # user_id unknown until after authentication
                "authentication",
                challenge,
                expires_at,
                self._get_timestamp(),
            ),
        )

        # Generate WebAuthn options
        options = generate_authentication_options(
            rp_id=self._rp_id,
            challenge=challenge,
            allow_credentials=allow_credentials if allow_credentials else None,
            user_verification=UserVerificationRequirement.PREFERRED,
        )

        # Convert to dict with proper bytes handling
        options_dict = json.loads(
            json.dumps(dataclasses.asdict(options), cls=_WebAuthnJSONEncoder)
        )

        return AuthenticationOptions(
            challenge_id=challenge_id,
            rp_id=self._rp_id,
            challenge=challenge,
            options_dict=options_dict,
        )

    def verify_authentication(
        self,
        challenge_id: str,
        credential_response: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Complete passkey authentication.

        Args:
            challenge_id: Challenge ID from authentication options
            credential_response: Client credential response

        Returns:
            Dict with user_id, credential info on success
        """
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("webauthn library not installed")

        # Verify and consume challenge
        challenge_row = self._db.fetch_one(
            """
            SELECT * FROM auth_passkey_challenges
            WHERE challenge_id = ? AND challenge_type = ? AND used = 0
            """,
            (challenge_id, "authentication"),
        )
        if not challenge_row:
            raise ValueError("Invalid or expired challenge")

        if challenge_row["expires_at"] < self._get_timestamp():
            raise ValueError("Challenge expired")

        challenge = challenge_row["challenge"]

        # Mark challenge as used with atomic check to prevent replay
        result = self._db.execute(
            "UPDATE auth_passkey_challenges SET used = 1 WHERE id = ? AND used = 0",
            (challenge_row["id"],),
        )
        if result.rowcount == 0:
            raise ValueError("Challenge already used - possible replay attack")

        # Get credential ID from response
        credential_id_b64 = credential_response.get("id", "")

        # Look up credential
        cred_row = self._db.fetch_one(
            "SELECT * FROM auth_passkeys WHERE credential_id = ? AND revoked = 0",
            (credential_id_b64,),
        )
        if not cred_row:
            raise ValueError("Unknown credential")

        credential_public_key = cred_row["credential_public_key"]
        current_sign_count = cred_row["sign_count"]

        # Verify the authentication response
        try:
            verification = verify_authentication_response(
                credential=credential_response,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=credential_public_key,
                credential_current_sign_count=current_sign_count,
            )
        except Exception as e:
            logger.error(f"Passkey authentication verification failed: {e}")
            raise ValueError(f"Authentication verification failed: {e}")

        # Update sign count with validation to detect credential cloning
        new_sign_count = verification.new_sign_count
        if new_sign_count <= current_sign_count:
            logger.warning(
                f"Sign counter did not increase for credential {credential_id_b64}: "
                f"{current_sign_count} -> {new_sign_count}. Possible credential cloning attack."
            )
            raise ValueError(
                "Sign counter validation failed - possible credential cloning"
            )

        now = self._get_timestamp()

        self._db.execute(
            "UPDATE auth_passkeys SET sign_count = ?, last_used_at = ? WHERE id = ?",
            (new_sign_count, now, cred_row["id"]),
        )

        user_id = cred_row["user_id"]

        logger.info(f"Passkey authentication successful for user {user_id}")

        return {
            "user_id": user_id,
            "credential_id": credential_id_b64,
            "device_name": cred_row["device_name"],
            "backed_up": bool(cred_row["backed_up"]),
        }

    def list_passkeys(self, user_id: int) -> List[PasskeyCredential]:
        """List all passkeys for a user."""
        if not WEBAUTHN_AVAILABLE:
            return []

        rows = self._db.fetch_all(
            """
            SELECT * FROM auth_passkeys
            WHERE user_id = ? AND revoked = 0
            ORDER BY created_at DESC
            """,
            (user_id,),
        )

        passkeys = []
        for row in rows:
            passkeys.append(
                PasskeyCredential(
                    id=row["id"],
                    user_id=row["user_id"],
                    credential_id=row["credential_id"],
                    credential_public_key=row["credential_public_key"],
                    sign_count=row["sign_count"],
                    device_type=row["device_type"],
                    device_name=row["device_name"],
                    aaguid=row["aaguid"],
                    transports=[t for t in row["transports"].split(",") if t.strip()]
                    if row["transports"]
                    else [],
                    backed_up=bool(row["backed_up"]),
                    created_at=row["created_at"],
                    last_used_at=row["last_used_at"],
                    revoked=bool(row["revoked"]),
                )
            )

        return passkeys

    def revoke_passkey(self, user_id: int, passkey_id: int) -> bool:
        """Revoke a passkey."""
        if not WEBAUTHN_AVAILABLE:
            return False

        self._db.execute(
            "UPDATE auth_passkeys SET revoked = 1 WHERE id = ? AND user_id = ?",
            (passkey_id, user_id),
        )

        # Check if any passkeys remain
        remaining = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM auth_passkeys WHERE user_id = ? AND revoked = 0",
            (user_id,),
        )

        if remaining and remaining["count"] == 0:
            # Disable passkey flag on user
            self._db.execute(
                "UPDATE auth_users SET passkey_enabled = 0 WHERE id = ?",
                (user_id,),
            )

        logger.info(f"Passkey {passkey_id} revoked for user {user_id}")
        return True

    def rename_passkey(self, user_id: int, passkey_id: int, new_name: str) -> bool:
        """Rename a passkey."""
        if not WEBAUTHN_AVAILABLE:
            return False

        self._db.execute(
            "UPDATE auth_passkeys SET device_name = ? WHERE id = ? AND user_id = ?",
            (new_name, passkey_id, user_id),
        )

        return True

    def cleanup_expired_challenges(self) -> int:
        """Clean up expired challenges."""
        now = self._get_timestamp()
        result = self._db.execute(
            "DELETE FROM auth_passkey_challenges WHERE expires_at < ?",
            (now,),
        )
        count = getattr(result, "rowcount", 0)
        if count > 0:
            logger.debug(f"Cleaned up {count} expired passkey challenges")
        return count
