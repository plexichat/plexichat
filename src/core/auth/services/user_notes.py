"""
User notes service - Business logic for private user notes.
"""

from typing import Optional, List, Dict, Any
import time

from src.core.base import SnowflakeID
from src.utils.encryption import encrypt_data, decrypt_data
import utils.logger as logger


class UserNotesService:
    """Service for user notes operations."""

    def __init__(self, db: Any, encryption_enabled: bool = True) -> None:
        """Initialize user notes service."""
        from .repositories.user_notes import UserNotesRepository  # type: ignore

        self._db = db
        self._repo = UserNotesRepository(db)
        self._encryption_enabled = encryption_enabled

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

        # Encrypt the note
        note_encrypted = None
        if self._encryption_enabled:
            try:
                note_encrypted = encrypt_data(note)
            except Exception as e:
                logger.warning(f"Failed to encrypt user note: {e}")
                # Fall back to plaintext if encryption fails

        now = int(time.time() * 1000)
        note_id = self._generate_id()

        self._repo.create(
            note_id=note_id,
            user_id=user_id,
            target_user_id=target_user_id,
            note_encrypted=note_encrypted or note,
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

        # Encrypt the note
        note_encrypted = None
        if self._encryption_enabled:
            try:
                note_encrypted = encrypt_data(note)
            except Exception as e:
                logger.warning(f"Failed to encrypt user note: {e}")

        now = int(time.time() * 1000)
        self._repo.update(existing["id"], note_encrypted or note, now)

        logger.debug(f"User {user_id} updated note about user {target_user_id}")
        note_data = self.get_note(user_id, target_user_id)
        if note_data is None:
            raise RuntimeError("Failed to retrieve updated note")
        return note_data

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

        # Decrypt the note
        note_content = row["note_encrypted"]
        if self._encryption_enabled and row["note_encrypted"]:
            try:
                note_content = decrypt_data(row["note_encrypted"])
            except Exception as e:
                logger.warning(f"Failed to decrypt user note: {e}")
                # Fall back to encrypted text if decryption fails

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "target_user_id": row["target_user_id"],
            "note": note_content,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_all_notes(self, user_id: SnowflakeID) -> List[Dict[str, Any]]:
        """
        Get all notes created by a user.

        Args:
            user_id: ID of the user

        Returns:
            List of note data with decrypted content
        """
        rows = self._repo.get_all(user_id)

        notes = []
        for row in rows:
            # Decrypt the note
            note_content = row["note_encrypted"]
            if self._encryption_enabled and row["note_encrypted"]:
                try:
                    note_content = decrypt_data(row["note_encrypted"])
                except Exception as e:
                    logger.warning(f"Failed to decrypt user note: {e}")

            notes.append(
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "target_user_id": row["target_user_id"],
                    "note": note_content,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )

        return notes

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
        from src.utils.encryption import generate_snowflake_id

        return generate_snowflake_id()
