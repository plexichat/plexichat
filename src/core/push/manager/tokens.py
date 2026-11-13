"""Push token lifecycle: registration, lookup, and at-rest migration."""

import time
from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.notifications.manager.push import (
    decrypt_push_token,
    encrypt_push_token,
)
from src.utils.encryption import blind_index, generate_snowflake_id

from ..exceptions import PermissionDeniedError
from .base import PUSH_TOKEN_BLIND_INDEX_SCOPE, PushManagerBase


class TokenMixin(PushManagerBase):
    """Register, refresh, list, and retire device push tokens."""

    def _migrate_legacy_token_rows(self) -> None:
        """Re-envelope legacy plaintext token rows (H3 hardening).

        Rows written before encryption-at-rest landed store the raw FCM /
        APNs / Web-Push credential verbatim and have no ``token_index``.
        On startup we re-encrypt them (token + p256dh + auth) and backfill
        the blind index so lookups keep working. Failures are logged and
        skipped — a row we cannot read must never break registration.
        """
        try:
            rows = self._db.fetch_all(
                """SELECT id, token, p256dh, auth FROM push_tokens
                   WHERE token_index IS NULL OR token_index = ''"""
            )
        except Exception:
            return
        for row in rows:
            try:
                plain_token = decrypt_push_token(row["token"])
                if not plain_token:
                    continue
                # Legacy rows store p256dh/auth raw; decrypt_push_token
                # passes them through (with a loud warning) unchanged.
                plain_p256dh = (
                    decrypt_push_token(row["p256dh"]) if row["p256dh"] else None
                )
                plain_auth = decrypt_push_token(row["auth"]) if row["auth"] else None
                self._db.execute(
                    """UPDATE push_tokens
                       SET token = ?, token_index = ?, p256dh = ?, auth = ?
                       WHERE id = ?""",
                    (
                        encrypt_push_token(plain_token),
                        self._token_blind_index(plain_token),
                        encrypt_push_token(plain_p256dh) if plain_p256dh else None,
                        encrypt_push_token(plain_auth) if plain_auth else None,
                        row["id"],
                    ),
                )
                logger.debug(f"Re-encrypted legacy push token row {row['id']} at rest")
            except Exception as e:
                logger.warning(
                    f"Failed to migrate legacy push token row {row.get('id')}: {e}"
                )

    @staticmethod
    def _token_blind_index(plain_token: str) -> str:
        return blind_index(plain_token, PUSH_TOKEN_BLIND_INDEX_SCOPE)

    @staticmethod
    def _plaintext_for_hint(stored_token: Any) -> Optional[str]:
        """Return the plaintext a ``token_hint`` may be derived from.

        ``_sanitize_token_record`` runs on two record shapes:
        ``register_token`` hands it the raw ``push_tokens`` row, where the
        credentials are still wrapped in their ``ENC:v1:`` envelope, while
        ``get_user_tokens`` hands it a record whose credentials were already
        decrypted for delivery. Taking the last characters of the stored
        value therefore published the tail of the *ciphertext* on the
        register path: AES-256-GCM draws a fresh nonce per call, so that
        value rotated on every encrypt and identified nothing about the
        device. Unwrap the envelope first (a no-op for already-decrypted
        records); an unwrappable envelope yields no hint rather than a
        misleading one.
        """
        if not stored_token:
            return None
        value = str(stored_token)
        try:
            if value.startswith(("ENC:", "enc:", "PLAIN:")):
                value = decrypt_push_token(value)
        except Exception as e:
            logger.warning(f"Could not derive a push token hint: {e}")
            return None
        return value or None

    @staticmethod
    def _sanitize_token_record(record: Dict[str, Any]) -> Dict[str, Any]:
        """Strip raw credentials from a token record for API responses.

        The stored ``token``, ``p256dh`` and ``auth`` values are device
        credentials; they are never echoed back to clients. A short,
        non-reversible ``token_hint`` (last 4 chars) is exposed instead so
        users can tell devices apart.
        """
        sanitized = {
            k: v
            for k, v in dict(record or {}).items()
            if k not in ("token", "p256dh", "auth", "token_index")
        }
        plain_token = TokenMixin._plaintext_for_hint((record or {}).get("token"))
        if plain_token:
            sanitized["token_hint"] = f"…{plain_token[-4:]}"
        return sanitized

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def register_token(
        self,
        user_id: int,
        token: str,
        platform: str,
        device_id: Optional[str] = None,
        app_version: Optional[str] = None,
        p256dh: Optional[str] = None,
        auth: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Register a push notification token for a user's device.

        Args:
            user_id: ID of the user
            token: Push notification token (FCM/APNs token or Web Push endpoint)
            platform: Platform type (ios, android, web)
            device_id: Optional device identifier
            app_version: Optional app version string
            p256dh: Web Push subscription P-256 ECDH public key (web only)
            auth: Web Push subscription auth secret (web only)

        Returns:
            Sanitized token record dict (raw token/p256dh/auth credentials
            are never included — see :meth:`_sanitize_token_record`).
        """
        platform = platform.lower()
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Unsupported platform: {platform}. Use: {self.SUPPORTED_PLATFORMS}"
            )

        now = self._get_timestamp()
        # SECURITY: credentials are encrypted at rest (ENC:v1 envelope);
        # lookups go through a keyed blind index over the plaintext.
        stored_token = encrypt_push_token(token)
        token_idx = self._token_blind_index(token)
        stored_p256dh = encrypt_push_token(p256dh) if p256dh else None
        stored_auth = encrypt_push_token(auth) if auth else None

        # Check if this token already exists (for any user - re-registration)
        existing = self._db.fetch_one(
            "SELECT id, user_id FROM push_tokens WHERE token_index = ?",
            (token_idx,),
        )
        if existing:
            # A push token is a credential bound to the device that generated
            # it. Re-registering a token that already belongs to a *different*
            # user would let account B hijack account A's notifications, so we
            # reject cross-user re-registration outright.
            if existing["user_id"] != user_id:
                raise PermissionDeniedError(
                    "This push token is already registered to another account"
                )
            # Same user re-registering (e.g. refreshed subscription) — update.
            self._db.execute(
                """UPDATE push_tokens SET platform = ?, device_id = ?,
                   app_version = ?, token = ?, p256dh = ?, auth = ?,
                   updated_at = ?, last_used_at = ?
                   WHERE id = ?""",
                (
                    platform,
                    device_id,
                    app_version,
                    stored_token,
                    stored_p256dh,
                    stored_auth,
                    now,
                    now,
                    existing["id"],
                ),
            )
            row = self._db.fetch_one(
                "SELECT * FROM push_tokens WHERE id = ?", (existing["id"],)
            )
            return self._sanitize_token_record(dict(row)) if row else {}

        # Check token limit per user (only enforced when a brand new token row
        # is created — re-registering an existing token never adds a row).
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM push_tokens WHERE user_id = ?",
            (user_id,),
        )
        count = count_row["count"] if count_row else 0
        if count >= self.MAX_TOKENS_PER_USER:
            oldest = self._db.fetch_one(
                "SELECT id FROM push_tokens WHERE user_id = ? ORDER BY updated_at ASC LIMIT 1",
                (user_id,),
            )
            if oldest:
                self._db.execute(
                    "DELETE FROM push_tokens WHERE id = ?", (oldest["id"],)
                )

        token_id = self._generate_id()
        self._db.execute(
            """INSERT INTO push_tokens
               (id, user_id, token, token_index, platform, device_id, app_version,
                p256dh, auth, created_at, updated_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                user_id,
                stored_token,
                token_idx,
                platform,
                device_id,
                app_version,
                stored_p256dh,
                stored_auth,
                now,
                now,
                now,
            ),
        )

        logger.debug(f"Push token registered for user {user_id} on {platform}")
        row = self._db.fetch_one("SELECT * FROM push_tokens WHERE id = ?", (token_id,))
        return self._sanitize_token_record(dict(row)) if row else {}

    def unregister_token(self, user_id: int, token: str) -> bool:
        """Remove a push notification token."""
        self._db.execute(
            "DELETE FROM push_tokens WHERE user_id = ? AND token_index = ?",
            (user_id, self._token_blind_index(token)),
        )
        return True

    def unregister_token_by_id(self, user_id: int, token_id: int) -> bool:
        """Remove a push token row by its id (must belong to ``user_id``).

        List responses only expose the row id plus a non-reversible
        ``token_hint`` — clients unregister devices by id because the raw
        token value is never sent back to them.
        """
        self._db.execute(
            "DELETE FROM push_tokens WHERE user_id = ? AND id = ?",
            (user_id, token_id),
        )
        return True

    def get_user_tokens(
        self, user_id: int, platform: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all push tokens for a user, decrypted for internal delivery.

        The returned records contain the plaintext credentials required by
        FCM/APNs/Web-Push senders — they must NEVER be serialised into an
        API response (use :meth:`_sanitize_token_record` first).
        """
        if platform:
            rows = self._db.fetch_all(
                "SELECT * FROM push_tokens WHERE user_id = ? AND platform = ?",
                (user_id, platform),
            )
        else:
            rows = self._db.fetch_all(
                "SELECT * FROM push_tokens WHERE user_id = ?",
                (user_id,),
            )
        results = []
        for row in rows:
            record = dict(row)
            try:
                record["token"] = decrypt_push_token(record.get("token") or "")
                if record.get("p256dh"):
                    record["p256dh"] = decrypt_push_token(record["p256dh"])
                if record.get("auth"):
                    record["auth"] = decrypt_push_token(record["auth"])
            except Exception as e:
                logger.warning(
                    f"Failed to decrypt push token row {record.get('id')}: {e}"
                )
                continue
            results.append(record)
        return results
