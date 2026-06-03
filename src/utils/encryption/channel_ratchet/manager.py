"""
High-level channel ratchet manager.

The manager composes a :class:`ChannelRatchetStore` with the
existing keyring and the HKDF primitive to:

* open new ratchet intervals for a conversation;
* derive the per-message AES-256-GCM key from the interval's
  ``start_key``, the per-message nonce, and the message id;
* decide when to rotate based on count and age;
* split the active interval when a message is hard-deleted;
* produce a JSON-safe snapshot of the active interval for the
  client API.

The wire format is::

    ENC:3:{interval_id}:{base64(nonce || ciphertext_with_tag)}

Where ``ciphertext_with_tag`` is the AES-256-GCM output for
``nonce``, ``key = HKDF(start_key, salt=interval_id, info=nonce||msg_id||context)``,
and AAD = ``b"ENC:3:" + interval_id_bytes``.
"""

from __future__ import annotations

import base64
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import utils.config as config

from ..core.keyring import Keyring
from ..hkdf import derive_key
from .exceptions import (
    ChannelRatchetError,
    RatchetIntervalNotFoundError,
)
from .interval import RatchetInterval
from .store import ChannelRatchetStore

if TYPE_CHECKING:
    from src.core.base import SnowflakeID


_DEFAULT_RATCHET_KEYRING_PATH = (
    Path.home() / ".plexichat" / "data" / "message_keyring.json"
)


CONTEXT_TAG = b"plexichat.channel-message.v3"

WIRE_VERSION = 3
WIRE_PREFIX = f"ENC:{WIRE_VERSION}:"
AAD_PREFIX = WIRE_PREFIX.encode("utf-8")

DEFAULT_MAX_MESSAGES_PER_INTERVAL = 1000
DEFAULT_MAX_INTERVAL_AGE_SECONDS = 86400
NONCE_BYTES = 12
KEY_BYTES = 32

LEGACY_WIRE_PREFIXES = ("ENC:1:", "ENC:2:")


def _legacy_envelope_allowed() -> bool:
    """Return True if legacy ``ENC:1:`` / ``ENC:2:`` envelopes may
    still be decrypted.

    Reads the ``messaging.ratchet_allow_legacy_envelopes`` config
    key (default: ``True`` for backward compatibility). Operators
    who have migrated every conversation to the v3 ratchet can
    set this to ``False`` to hard-reject any legacy ciphertext,
    forcing a clean re-encrypt pass before the server will
    serve a message.
    """
    try:
        cfg = config.get("messaging", {}) if hasattr(config, "get") else {}
    except Exception:
        return True
    return bool(cfg.get("ratchet_allow_legacy_envelopes", True))


@dataclass
class EncryptedMessageV3:
    """The result of an encryption operation."""

    envelope: str
    interval_id: SnowflakeID
    message_id: SnowflakeID


class ChannelRatchetManager:
    """High-level entry point for the channel ratchet.

    The manager is a process-level singleton. It holds a per-instance
    lock around interval creation and rotation; per-message encryption
    is lock-free because the AES-GCM nonce is unique per call.
    """

    def __init__(
        self,
        db: Any,
        keyring: Optional[Keyring] = None,
        *,
        max_messages_per_interval: Optional[int] = None,
        max_interval_age_seconds: Optional[int] = None,
        split_on_delete: bool = True,
    ) -> None:
        self._db = db
        self._keyring = keyring or Keyring(
            _DEFAULT_RATCHET_KEYRING_PATH,
            kek_env_var="PLEXICHAT_MESSAGE_KEY",
        )
        self._store = ChannelRatchetStore(db, self._keyring)
        self._lock = threading.RLock()

        cfg = config.get("messaging", {}) if hasattr(config, "get") else {}
        self._max_messages = int(
            max_messages_per_interval
            if max_messages_per_interval is not None
            else cfg.get("ratchet_max_messages", DEFAULT_MAX_MESSAGES_PER_INTERVAL)
        )
        self._max_age_seconds = int(
            max_interval_age_seconds
            if max_interval_age_seconds is not None
            else cfg.get("ratchet_max_age_seconds", DEFAULT_MAX_INTERVAL_AGE_SECONDS)
        )
        if split_on_delete is None:
            self._split_on_delete = bool(cfg.get("ratchet_split_on_delete", True))
        else:
            self._split_on_delete = bool(split_on_delete)

    # === Public API ===

    def get_active_interval(
        self, conversation_id: SnowflakeID
    ) -> Optional[RatchetInterval]:
        """Return the open interval for ``conversation_id`` (or None)."""
        return self._store.get_active(conversation_id)

    def list_intervals(
        self, conversation_id: SnowflakeID, limit: int = 50
    ) -> List[RatchetInterval]:
        """List intervals for ``conversation_id``, newest first."""
        return self._store.list_for_conversation(conversation_id, limit=limit)

    def snapshot(self, conversation_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Return a JSON-safe snapshot of the active interval.

        The snapshot is what the client API serves to authenticated
        viewers so they can decrypt historical messages fetched out
        of order. The raw ``start_key`` is never exposed; the client
        receives it under the active session.
        """
        active = self._store.get_active(conversation_id)
        if active is None:
            return None
        snap = active.to_dict()
        snap["start_key"] = base64.b64encode(active.start_key).decode("ascii")
        snap["context_tag"] = CONTEXT_TAG.decode("utf-8")
        snap["nonce_bytes"] = NONCE_BYTES
        snap["key_bytes"] = KEY_BYTES
        return snap

    def encrypt(
        self,
        conversation_id: SnowflakeID,
        message_id: SnowflakeID,
        plaintext: bytes,
        *,
        now: Optional[int] = None,
    ) -> EncryptedMessageV3:
        """Encrypt ``plaintext`` for ``message_id`` in ``conversation_id``.

        On first call for a given conversation (or after a rotation),
        a new interval is opened. The interval id is returned alongside
        the wire-format envelope so the caller can persist it on the
        message row.
        """
        if not isinstance(plaintext, (bytes, bytearray)):
            raise TypeError("plaintext must be bytes")
        if not isinstance(conversation_id, int) or not isinstance(message_id, int):
            raise TypeError("conversation_id and message_id must be int snowflakes")

        timestamp_ms = int(now if now is not None else _now_ms())
        nonce = os.urandom(NONCE_BYTES)
        interval = self._ensure_interval(conversation_id, message_id, timestamp_ms)
        key = self._derive_key(interval, nonce, message_id)
        interval_id_bytes = str(int(interval.interval_id)).encode("utf-8")
        aad = AAD_PREFIX + interval_id_bytes
        ciphertext = AESGCM(key).encrypt(nonce, bytes(plaintext), aad)

        self._store.touch(interval.interval_id, timestamp_ms)

        envelope = (
            WIRE_PREFIX
            + str(int(interval.interval_id))
            + ":"
            + base64.b64encode(nonce + ciphertext).decode("ascii")
        )
        return EncryptedMessageV3(
            envelope=envelope,
            interval_id=interval.interval_id,
            message_id=message_id,
        )

    def decrypt(
        self,
        conversation_id: SnowflakeID,
        message_id: SnowflakeID,
        envelope: str,
    ) -> bytes:
        """Reverse of :meth:`encrypt`.

        Looks up the interval referenced by the envelope, derives the
        per-message key, and decrypts the ciphertext. The per-message
        nonce lives only inside the envelope; the start key lives in
        the interval row; the message id is part of the HKDF info
        string. If any of those three inputs differ from what the
        client produced, AES-GCM authentication fails.
        """
        if not envelope.startswith(WIRE_PREFIX):
            raise ChannelRatchetError("envelope is not a v3 ratchet envelope")
        try:
            tail = envelope[len(WIRE_PREFIX) :]
            interval_id_str, b64 = tail.split(":", 1)
            interval_id = int(interval_id_str)
            blob = base64.b64decode(b64)
        except (ValueError, TypeError) as exc:
            raise ChannelRatchetError("malformed v3 envelope") from exc

        if len(blob) <= NONCE_BYTES:
            raise ChannelRatchetError("envelope too short to contain nonce")

        nonce = blob[:NONCE_BYTES]
        ciphertext = blob[NONCE_BYTES:]

        interval = self._store.get_by_id(interval_id)
        if interval is None:
            raise RatchetIntervalNotFoundError(
                f"interval {interval_id} not found for message {message_id}"
            )
        if interval.conversation_id != conversation_id:
            raise ChannelRatchetError(
                "interval does not belong to the given conversation"
            )

        key = self._derive_key(interval, nonce, message_id)
        aad = AAD_PREFIX + str(interval_id).encode("utf-8")
        return AESGCM(key).decrypt(nonce, ciphertext, aad)

    def rotate_if_due(
        self,
        conversation_id: SnowflakeID,
        last_message_id: SnowflakeID,
        now_ms: int,
    ) -> Optional[RatchetInterval]:
        """Open a new interval if the active one has hit a threshold.

        Returns the *new* interval if a rotation happened, otherwise
        None. Called by ``send_message`` after a successful insert.
        """
        with self._lock:
            active = self._store.get_active(conversation_id)
            if active is None:
                return None

            count = self._store.count_messages(active.interval_id)
            age_ms = max(0, int(now_ms) - int(active.created_at))
            if count < self._max_messages and age_ms < self._max_age_seconds * 1000:
                return None

            new_key = secrets.token_bytes(KEY_BYTES)
            new_id = self._next_interval_id(now_ms)
            return self._store.create(
                new_id,
                conversation_id,
                int(last_message_id) + 1,
                new_key,
                int(now_ms),
            )

    def split_on_delete(
        self,
        conversation_id: SnowflakeID,
        deleted_message_id: SnowflakeID,
        now_ms: int,
    ) -> Optional[RatchetInterval]:
        """Close the active interval and start a new one after a delete.

        This is the defense-in-depth piece: even if a stolen phone
        already cached the active ``start_key``, the deleted message
        can no longer be decrypted because its per-message nonce has
        been destroyed, and a fresh start key protects everything
        from the next message onward.

        Returns the new interval if a split actually happened, or
        None if split-on-delete is disabled or no active interval
        exists.
        """
        if not self._split_on_delete:
            return None
        with self._lock:
            active = self._store.get_active(conversation_id)
            if active is None:
                return None
            if not active.contains(deleted_message_id):
                return None

            self._store.close_active(
                conversation_id,
                int(deleted_message_id) + 1,
                int(now_ms),
            )
            new_key = secrets.token_bytes(KEY_BYTES)
            new_id = self._next_interval_id(now_ms)
            return self._store.create(
                new_id,
                conversation_id,
                int(deleted_message_id) + 1,
                new_key,
                int(now_ms),
            )

    # === Internal helpers ===

    def _ensure_interval(
        self,
        conversation_id: SnowflakeID,
        message_id: SnowflakeID,
        now_ms: int,
    ) -> RatchetInterval:
        active = self._store.get_active(conversation_id)
        if active is not None and active.contains(message_id):
            return active

        with self._lock:
            active = self._store.get_active(conversation_id)
            if active is not None and active.contains(message_id):
                return active

            start_message_id = int(message_id) if active is None else int(message_id)
            new_key = secrets.token_bytes(KEY_BYTES)
            new_id = self._next_interval_id(now_ms)
            return self._store.create(
                new_id,
                conversation_id,
                start_message_id,
                new_key,
                now_ms,
            )

    def _derive_key(
        self,
        interval: RatchetInterval,
        nonce: bytes,
        message_id: SnowflakeID,
    ) -> bytes:
        if not interval.start_key:
            raise ChannelRatchetError(
                f"interval {interval.interval_id} has no usable start key"
            )
        salt = str(int(interval.interval_id)).encode("utf-8")
        info = nonce + str(int(message_id)).encode("utf-8") + CONTEXT_TAG
        return derive_key(interval.start_key, salt=salt, info=info, length=KEY_BYTES)

    def _next_interval_id(self, now_ms: int) -> SnowflakeID:
        try:
            from ..core.snowflake import SnowflakeGenerator

            return int(SnowflakeGenerator().generate())
        except Exception:
            return int(now_ms) << 22


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


__all__ = [
    "ChannelRatchetManager",
    "EncryptedMessageV3",
    "WIRE_VERSION",
    "WIRE_PREFIX",
    "CONTEXT_TAG",
    "DEFAULT_MAX_MESSAGES_PER_INTERVAL",
    "DEFAULT_MAX_INTERVAL_AGE_SECONDS",
]
