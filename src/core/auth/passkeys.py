"""
Passkey (WebAuthn/FIDO2) authentication module.

This module handles passkey registration, authentication, listing, revocation, and renaming.
"""

from typing import Optional, Dict, List, Any

import base64
import json
import os
import secrets
import time
from dataclasses import dataclass

import utils.config as config
import utils.logger as logger

from src.utils.encryption import EncryptionManager

from ._lazy import _get_auth_manager


def generate_passkey_registration_options(
    user_id: int, device_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    return (
        _get_auth_manager()
        .get_instance()
        .generate_passkey_registration_options(user_id, device_name)
    )


def verify_passkey_registration(
    user_id: int,
    challenge_id: str,
    credential_response: Dict[str, Any],
    ip_address: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return (
        _get_auth_manager()
        .get_instance()
        .verify_passkey_registration(
            user_id, challenge_id, credential_response, ip_address
        )
    )


def generate_passkey_authentication_options(
    username: Optional[str] = None,
) -> Dict[str, Any]:
    return (
        _get_auth_manager()
        .get_instance()
        .generate_passkey_authentication_options(username)
    )


def verify_passkey_authentication(
    challenge_id: str,
    credential_response: Dict[str, Any],
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    return (
        _get_auth_manager()
        .get_instance()
        .verify_passkey_authentication(
            challenge_id, credential_response, ip_address, user_agent
        )
    )


def list_passkeys(user_id: int) -> List[Dict[str, Any]]:
    return _get_auth_manager().get_instance().list_passkeys(user_id)


def revoke_passkey(
    user_id: int, passkey_id: int, ip_address: Optional[str] = None
) -> bool:
    return (
        _get_auth_manager()
        .get_instance()
        .revoke_passkey(user_id, passkey_id, ip_address)
    )


def rename_passkey(user_id: int, passkey_id: int, new_name: str) -> bool:
    return (
        _get_auth_manager().get_instance().rename_passkey(user_id, passkey_id, new_name)
    )


try:
    from webauthn import (  # type: ignore
        generate_authentication_options,
        generate_registration_options,
        verify_authentication_response,
        verify_registration_response,
    )
    from webauthn.helpers.structs import (  # type: ignore
        AttestationConveyancePreference,
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    WEBAUTHN_AVAILABLE = True
except ImportError:
    WEBAUTHN_AVAILABLE = False
    logger.warning("webauthn library not installed. Passkey support disabled.")

    from typing import Any

    generate_authentication_options: Any = None  # type: ignore
    generate_registration_options: Any = None  # type: ignore
    verify_authentication_response: Any = None  # type: ignore
    verify_registration_response: Any = None  # type: ignore
    AttestationConveyancePreference: Any = None  # type: ignore
    AuthenticatorSelectionCriteria: Any = None  # type: ignore
    PublicKeyCredentialDescriptor: Any = None  # type: ignore
    ResidentKeyRequirement: Any = None  # type: ignore
    UserVerificationRequirement: Any = None  # type: ignore


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

    def __init__(self, db, crypto: Optional[EncryptionManager] = None):
        """Initialize the passkey manager.

        Args:
            db: Database instance
            crypto: Optional shared EncryptionManager (avoids double argon2 cost)
        """
        self._db = db
        self._crypto = crypto if crypto is not None else EncryptionManager()
        self._config = config.get("authentication", {}).get("passkeys", {})
        self._challenge_ttl = self._config.get("challenge_ttl_seconds", 300)

        # Get RP configuration
        server_config = config.get("server", {})
        self._rp_name = self._config.get("rp_name", "Plexichat")
        self._rp_id = self._config.get("rp_id", server_config.get("host", "localhost"))

        if not WEBAUTHN_AVAILABLE:
            logger.error(
                "PasskeyManager initialized but webauthn library not available"
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

        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_passkeys_user ON auth_passkeys(user_id)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_passkeys_credential ON auth_passkeys(credential_id)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_passkey_challenges_expires ON auth_passkey_challenges(expires_at)"
        )

        try:
            self._db.execute(
                "ALTER TABLE auth_users ADD COLUMN passkey_enabled INTEGER DEFAULT 0"
            )
        except Exception:  # nosec B110
            pass

        try:
            self._db.execute(
                "ALTER TABLE auth_users ADD COLUMN webauthn_user_handle TEXT"
            )
        except Exception:  # nosec B110
            pass

    def generate_registration_options(
        self,
        user_id: int,
        username: str,
        device_name: Optional[str] = None,
    ) -> Optional[RegistrationOptions]:
        """Generate WebAuthn registration options for a user."""
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("webauthn library not installed")

        self._ensure_tables()

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

        challenge = os.urandom(32)
        challenge_id = secrets.token_urlsafe(16)
        expires_at = self._get_timestamp() + (self._challenge_ttl * 1000)

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

        options_dict = json.loads(options.json())

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
        """Complete passkey registration."""
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("webauthn library not installed")

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

        challenge = challenge_row["challenge"]
        device_name = challenge_row.get("device_name")

        self._db.execute(
            "UPDATE auth_passkey_challenges SET used = 1 WHERE id = ?",
            (challenge_row["id"],),
        )

        try:
            verification = verify_registration_response(
                credential=credential_response,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._config.get("origin", f"https://{self._rp_id}"),
            )
        except Exception as e:
            logger.error(f"Passkey registration verification failed: {e}")
            raise ValueError(f"Registration verification failed: {e}")

        credential_id = self._base64url_encode(verification.credential_id)
        credential_public_key = verification.credential_public_key
        sign_count = verification.sign_count

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
        """Generate authentication options for passkey login."""
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("webauthn library not installed")

        self._ensure_tables()

        challenge = os.urandom(32)
        challenge_id = secrets.token_urlsafe(16)
        expires_at = self._get_timestamp() + (self._challenge_ttl * 1000)

        allow_credentials = []
        if username:
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

        self._db.execute(
            """
            INSERT INTO auth_passkey_challenges
            (id, challenge_id, user_id, challenge_type, challenge, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._generate_id(),
                challenge_id,
                None,
                "authentication",
                challenge,
                expires_at,
                self._get_timestamp(),
            ),
        )

        options = generate_authentication_options(
            rp_id=self._rp_id,
            challenge=challenge,
            allow_credentials=allow_credentials if allow_credentials else None,
            user_verification=UserVerificationRequirement.PREFERRED,
        )

        options_dict = json.loads(options.json())

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
        """Complete passkey authentication."""
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("webauthn library not installed")

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

        self._db.execute(
            "UPDATE auth_passkey_challenges SET used = 1 WHERE id = ?",
            (challenge_row["id"],),
        )

        credential_id_b64 = credential_response.get("id", "")

        cred_row = self._db.fetch_one(
            "SELECT * FROM auth_passkeys WHERE credential_id = ? AND revoked = 0",
            (credential_id_b64,),
        )
        if not cred_row:
            raise ValueError("Unknown credential")

        credential_public_key = cred_row["credential_public_key"]
        current_sign_count = cred_row["sign_count"]

        try:
            verification = verify_authentication_response(
                credential=credential_response,
                expected_challenge=challenge,
                expected_rp_id=self._rp_id,
                expected_origin=self._config.get("origin", f"https://{self._rp_id}"),
                credential_public_key=credential_public_key,
                credential_current_sign_count=current_sign_count,
            )
        except Exception as e:
            logger.error(f"Passkey authentication verification failed: {e}")
            raise ValueError(f"Authentication verification failed: {e}")

        new_sign_count = verification.new_sign_count
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
                    transports=row["transports"].split(",")
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

        remaining = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM auth_passkeys WHERE user_id = ? AND revoked = 0",
            (user_id,),
        )

        if remaining and remaining["count"] == 0:
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


__all__ = [
    "PasskeyManager",
    "PasskeyCredential",
    "RegistrationOptions",
    "AuthenticationOptions",
    "generate_passkey_registration_options",
    "verify_passkey_registration",
    "generate_passkey_authentication_options",
    "verify_passkey_authentication",
    "list_passkeys",
    "revoke_passkey",
    "rename_passkey",
]
