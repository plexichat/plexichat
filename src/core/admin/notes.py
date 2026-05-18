"""
Admin Notes Versioning System - Provides versioned notes with markdown support.

This module handles:
- Version tracking for admin notes
- Markdown rendering support
- Note history and rollback
- Change tracking and reasons
"""

import logging
import time
from typing import Optional, List, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class NoteVersion:
    """Represents a version of an admin note."""

    id: int
    target_type: str
    target_id: int
    note_content: str
    note_format: str
    created_by: int
    created_at: int
    version_number: int
    change_reason: Optional[str] = None


class AdminNotesManager:
    """Manages versioned admin notes with markdown support."""

    def __init__(self, db):
        """Initialize the notes manager."""
        self.db = db

    def create_note(
        self,
        target_type: str,
        target_id: int,
        content: str,
        author_id: int,
        note_format: str = "plain",
        change_reason: Optional[str] = None,
    ) -> int:
        """
        Create a new note version.

        Args:
            target_type: Type of target (user, server, etc.)
            target_id: ID of the target
            content: Note content
            author_id: ID of the admin creating the note
            note_format: Format of the note (plain, markdown)
            change_reason: Reason for the note creation

        Returns:
            ID of the created note version
        """
        from src.utils.encryption import generate_snowflake_id

        now = int(time.time() * 1000)
        note_id = generate_snowflake_id()

        # Get current version number for this target
        version_number = self._get_next_version_number(target_type, target_id)

        self.db.execute(
            """
            INSERT INTO admin_notes_versioning 
            (id, target_type, target_id, note_content, note_format, created_by, created_at, version_number, change_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                note_id,
                target_type,
                target_id,
                content,
                note_format,
                author_id,
                now,
                version_number,
                change_reason,
            ),
        )

        # Update the target's current note
        self._update_target_note(target_type, target_id, content, note_format)

        logger.info(
            f"Created note version {version_number} for {target_type} {target_id} by admin {author_id}"
        )
        return note_id

    def update_note(
        self,
        target_type: str,
        target_id: int,
        content: str,
        author_id: int,
        note_format: str = "plain",
        change_reason: Optional[str] = None,
    ) -> int:
        """
        Update an existing note (creates new version).

        Args:
            target_type: Type of target (user, server, etc.)
            target_id: ID of the target
            content: New note content
            author_id: ID of the admin updating the note
            note_format: Format of the note (plain, markdown)
            change_reason: Reason for the note update

        Returns:
            ID of the created note version
        """
        from src.utils.encryption import generate_snowflake_id

        now = int(time.time() * 1000)
        note_id = generate_snowflake_id()

        # Get current version number for this target
        version_number = self._get_next_version_number(target_type, target_id)

        self.db.execute(
            """
            INSERT INTO admin_notes_versioning 
            (id, target_type, target_id, note_content, note_format, created_by, created_at, version_number, change_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                note_id,
                target_type,
                target_id,
                content,
                note_format,
                author_id,
                now,
                version_number,
                change_reason,
            ),
        )

        # Update the target's current note
        self._update_target_note(target_type, target_id, content, note_format)

        logger.info(
            f"Updated note to version {version_number} for {target_type} {target_id} by admin {author_id}"
        )
        return note_id

    def get_note_history(
        self, target_type: str, target_id: int, limit: int = 10
    ) -> List[NoteVersion]:
        """
        Get version history for a note.

        Args:
            target_type: Type of target (user, server, etc.)
            target_id: ID of the target
            limit: Maximum number of versions to return

        Returns:
            List of note versions, ordered by version number (newest first)
        """
        rows = self.db.fetch_all(
            """
            SELECT id, target_type, target_id, note_content, note_format, created_by, created_at, version_number, change_reason
            FROM admin_notes_versioning
            WHERE target_type = ? AND target_id = ?
            ORDER BY version_number DESC
            LIMIT ?
        """,
            (target_type, target_id, limit),
        )

        versions = []
        for row in rows:
            if isinstance(row, dict):
                version = NoteVersion(
                    id=row["id"],
                    target_type=row["target_type"],
                    target_id=row["target_id"],
                    note_content=row["note_content"],
                    note_format=row["note_format"],
                    created_by=row["created_by"],
                    created_at=row["created_at"],
                    version_number=row["version_number"],
                    change_reason=row.get("change_reason"),
                )
            else:
                version = NoteVersion(
                    id=row[0],
                    target_type=row[1],
                    target_id=row[2],
                    note_content=row[3],
                    note_format=row[4],
                    created_by=row[5],
                    created_at=row[6],
                    version_number=row[7],
                    change_reason=row[8] if len(row) > 8 else None,
                )
            versions.append(version)

        return versions

    def rollback_note(
        self,
        target_type: str,
        target_id: int,
        version_number: int,
        author_id: int,
        change_reason: Optional[str] = None,
    ) -> int:
        """
        Rollback a note to a specific version.

        Args:
            target_type: Type of target (user, server, etc.)
            target_id: ID of the target
            version_number: Version number to rollback to
            author_id: ID of the admin performing the rollback
            change_reason: Reason for the rollback

        Returns:
            ID of the new note version created by rollback
        """
        # Get the version to rollback to
        version = self.db.fetch_one(
            """
            SELECT note_content, note_format
            FROM admin_notes_versioning
            WHERE target_type = ? AND target_id = ? AND version_number = ?
        """,
            (target_type, target_id, version_number),
        )

        if not version:
            raise ValueError(
                f"Version {version_number} not found for {target_type} {target_id}"
            )

        if isinstance(version, dict):
            content = version["note_content"]
            note_format = version["note_format"]
        else:
            content = version[0]
            note_format = version[1]

        # Create new version with old content
        rollback_reason = change_reason or f"Rollback to version {version_number}"
        return self.update_note(
            target_type, target_id, content, author_id, note_format, rollback_reason
        )

    def delete_note_history(
        self, target_type: str, target_id: int, before_version: Optional[int] = None
    ) -> int:
        """
        Delete note history before a specific version.

        Args:
            target_type: Type of target (user, server, etc.)
            target_id: ID of the target
            before_version: Delete versions before this number (if None, delete all)

        Returns:
            Number of versions deleted
        """
        if before_version is not None:
            result = self.db.execute(
                """
                DELETE FROM admin_notes_versioning
                WHERE target_type = ? AND target_id = ? AND version_number < ?
            """,
                (target_type, target_id, before_version),
            )
        else:
            result = self.db.execute(
                """
                DELETE FROM admin_notes_versioning
                WHERE target_type = ? AND target_id = ?
            """,
                (target_type, target_id),
            )

        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        logger.info(
            f"Deleted {deleted_count} note versions for {target_type} {target_id}"
        )
        return deleted_count

    def _get_next_version_number(self, target_type: str, target_id: int) -> int:
        """Get the next version number for a target."""
        result = self.db.fetch_one(
            """
            SELECT MAX(version_number) as max_version
            FROM admin_notes_versioning
            WHERE target_type = ? AND target_id = ?
        """,
            (target_type, target_id),
        )

        if result:
            max_version = (
                result.get("max_version") if isinstance(result, dict) else result[0]
            )
            return (max_version or 0) + 1
        return 1

    def _update_target_note(
        self, target_type: str, target_id: int, content: str, note_format: str
    ):
        """Update the target's current note and format."""
        encrypt_internal_notes = False  # Get from config if needed

        if target_type == "user":
            if encrypt_internal_notes:
                from src.utils.encryption import encrypt_data

                encrypted = encrypt_data(content)
                self.db.execute(
                    """
                    UPDATE auth_users 
                    SET internal_notes = ?, internal_notes_encrypted = ?, internal_notes_format = ?
                    WHERE id = ?
                """,
                    (content, encrypted, note_format, target_id),
                )
            else:
                self.db.execute(
                    """
                    UPDATE auth_users 
                    SET internal_notes = ?, internal_notes_format = ?
                    WHERE id = ?
                """,
                    (content, note_format, target_id),
                )
        elif target_type == "ticket":
            if encrypt_internal_notes:
                from src.utils.encryption import encrypt_data

                encrypted = encrypt_data(content)
                self.db.execute(
                    """
                    UPDATE feedback 
                    SET internal_notes = ?, internal_notes_encrypted = ?, internal_notes_format = ?
                    WHERE id = ?
                """,
                    (content, encrypted, note_format, target_id),
                )
            else:
                self.db.execute(
                    """
                    UPDATE feedback 
                    SET internal_notes = ?, internal_notes_format = ?
                    WHERE id = ?
                """,
                    (content, note_format, target_id),
                )

    def render_markdown(self, content: str) -> str:
        """
        Render markdown content to HTML.

        Args:
            content: Markdown content to render

        Returns:
            HTML rendered content
        """
        try:
            import markdown  # type: ignore

            return markdown.markdown(content, extensions=["extra", "nl2br"])
        except ImportError:
            # If markdown library not available, return as-is
            logger.warning("Markdown library not available, returning plain text")
            return content
        except Exception as e:
            logger.warning(f"Markdown rendering error: {e}")
            return content
