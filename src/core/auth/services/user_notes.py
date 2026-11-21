"""
User notes service - Business logic for private user notes.
"""

from typing import Optional, List, Dict, Any

from src.core.base import SnowflakeID, timestamp_ms, generate_id
from src.utils.encryption import encrypt_data, decrypt_data
import utils.logger as logger


class UserNotesService:
    """Service for user notes operations."""

    # Non-reversible placeholder used when encryption-at-rest is enabled but
    # the cryptographic operation fails. It carries none of the note's content,
    # so a database backup or a reader of the column cannot recover the private
    # text exactly when the keyring is misconfigured.
    _ENCRYPTION_FAILURE_MARKER = "[unavailable]"

    def __init__(self, db: Any, encryption_enabled: bool = True) -> None:
        """Initialize user notes service."""
        from ..repositories.user_notes import UserNotesRepository

        self._db = db
        self._repo = UserNotesRepository(db)
        self._encryption_enabled = encryption_enabled

    def _encrypted_or_marker(self, note: str) -> str:
        """Return ciphertext for ``note``, or a content-free failure marker.

        When encryption is disabled, storing plaintext is an explicit policy
        choice and is preserved. When it is enabled, a failed encryption must
        NEVER fall back to plaintext: the previous ``note_encrypted or note``
        fallback wrote the note verbatim to the database whenever
        ``encrypt_data`` raised, defeating encryption-at-rest for private notes
        (which can hold sensitive moderation text) exactly when the keyring is
        unavailable.
        """
        if not self._encryption_enabled:
            return note
        try:
            encrypted = encrypt_data(note)
            if encrypted:
                return encrypted
        except Exception as e:
            logger.warning(f"Failed to encrypt user note: {e}")
        return self._ENCRYPTION_FAILURE_MARKER

    def create_note(
        self,
        user_id: SnowflakeID,
        target_user_id: SnowflakeID,
        note: str,
    ) -> Dict[str, Any]:
        """
        Create a new user note.

        Args:
            user_id: ID of the user creating the note
            target_user_id: ID of the user the note is about
            note: Note content (will be encrypted)

        Returns:
            Created note data

        Raises:
            ValueError: If note is empty or too long
        """
        if not note or not note.strip():
            raise ValueError("Note cannot be empty")

        if len(note) > 10000:
            raise ValueError("Note cannot exceed 10,000 characters")

        if user_id == target_user_id:
            raise ValueError("Cannot create notes about yourself")

        # Check if note already exists
        if self._repo.exists(user_id, target_user_id):
            return self.update_note(user_id, target_user_id, note)

        # Encrypt the note. Fail CLOSED: never persist the plaintext when
        # encryption is required but the cryptographic operation fails.
        note_encrypted = self._encrypted_or_marker(note)

        now = timestamp_ms()
        note_id = self._generate_id()

        self._repo.create(
            note_id=note_id,
            user_id=user_id,
            target_user_id=target_user_id,
            note_encrypted=note_encrypted,
            created_at=now,
            updated_at=now,
        )

        logger.debug(f"User {user_id} created note about user {target_user_id}")
        note_data = self.get_note(user_id, target_user_id)
        if note_data is None:
            raise RuntimeError("Failed to retrieve created note")
        return note_data

    def update_note(
        self,
        user_id: SnowflakeID,
        target_user_id: SnowflakeID,
        note: str,
    ) -> Dict[str, Any]:
        """
        Update an existing user note.

        Args:
            user_id: ID of the user updating the note
            target_user_id: ID of the user the note is about
            note: New note content (will be encrypted)

        Returns:
            Updated note data

        Raises:
            ValueError: If note is empty or too long
        """
        if not note or not note.strip():
            raise ValueError("Note cannot be empty")

        if len(note) > 10000:
            raise ValueError("Note cannot exceed 10,000 characters")

        existing = self._repo.get(user_id, target_user_id)
        if not existing:
            return self.create_note(user_id, target_user_id, note)

        # Encrypt the note with the same fail-closed boundary as create: a
        # failed encrypt_data() must never write the note verbatim.
        now = timestamp_ms()
        self._repo.update(existing["id"], self._encrypted_or_marker(note), now)

        logger.debug(f"User {user_id} updated note about user {target_user_id}")
        note_data = self.get_note(user_id, target_user_id)
        if note_data is None:
            raise RuntimeError("Failed to retrieve updated note")
        return note_data

    def _note_from_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Format a repository row into the public note shape."""
        # Decrypt the note
        note_content = row["note_encrypted"]
        if self._encryption_enabled and row["note_encrypted"]:
            try:
                note_content = decrypt_data(row["note_encrypted"])
            except Exception as e:
                # Fail CLOSED: never hand the raw stored value back as the
                # note. It may be a legacy plaintext row or a redaction marker
                # written when encryption failed, so returning it would undo
                # the encryption-at-rest contract at exactly the moment the
                # keyring cannot be read.
                logger.warning(f"Failed to decrypt user note: {e}")
                note_content = self._ENCRYPTION_FAILURE_MARKER

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "target_user_id": row["target_user_id"],
            "note": note_content,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_note(
        self,
        user_id: SnowflakeID,
        target_user_id: SnowflakeID,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a user's note about another user.

        Args:
            user_id: ID of the user who created the note
            target_user_id: ID of the user the note is about

        Returns:
            Note data with decrypted content, or None if not found
        """
        row = self._repo.get(user_id, target_user_id)
        if not row:
            return None
        return self._note_from_row(row)

    def get_notes_for_targets(
        self,
        user_id: SnowflakeID,
        target_user_ids: List[SnowflakeID],
        *,
        chunk_size: Optional[int] = None,
    ) -> Dict[SnowflakeID, Dict[str, Any]]:
        """Get one author's notes about many targets without an N+1 read.

        Replaces a loop of :meth:`get_note` calls with a handful of bounded
        ``IN`` queries. The repository keeps ``user_id`` scoping on every
        query, so no other author's notes can be returned. Results use the
        same shape as :meth:`get_note`, keyed by target user id; targets
        without a note are absent from the mapping.
        """
        kwargs = {} if chunk_size is None else {"chunk_size": chunk_size}
        rows = self._repo.get_many(user_id, target_user_ids, **kwargs)
        return {target_id: self._note_from_row(row) for target_id, row in rows.items()}

    def get_all_notes(self, user_id: SnowflakeID) -> List[Dict[str, Any]]:
        """
        Get all notes created by a user.

        Args:
            user_id: ID of the user

        Returns:
            List of note data with decrypted content
        """
        rows = self._repo.get_all(user_id)

        return [self._note_from_row(row) for row in rows]

    def delete_note(
        self,
        user_id: SnowflakeID,
        target_user_id: SnowflakeID,
    ) -> bool:
        """
        Delete a user note.

        Args:
            user_id: ID of the user who created the note
            target_user_id: ID of the user the note is about

        Returns:
            True if deleted
        """
        result = self._repo.delete(user_id, target_user_id)
        logger.debug(f"User {user_id} deleted note about user {target_user_id}")
        return result

    def _generate_id(self) -> SnowflakeID:
        """Generate a new Snowflake ID."""
        return generate_id()
