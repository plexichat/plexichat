"""
Voice message service - Business logic for voice messages.

Handles creating, validating, and managing voice message attachments
with proper duration limits, format validation, and transcription support.
"""

import time
import json
from typing import Optional, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id


class VoiceMessageService:
    """Service for managing voice messages."""

    MAX_VOICE_DURATION_MS = 600000  # 10 minutes max
    MIN_VOICE_DURATION_MS = 100  # 100ms minimum
    ALLOWED_FORMATS = {"ogg", "mp3", "wav", "webm", "opus"}
    MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
    SAMPLE_RATES = {8000, 12000, 16000, 24000, 44100, 48000}

    def __init__(self, db, attachment_svc=None):
        self._db = db
        self._attachment_svc = attachment_svc

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def validate_voice_attachment(
        self,
        filename: str,
        content_type: str,
        size: int,
        duration_ms: Optional[int] = None,
        waveform: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate a voice message attachment before processing.

        Args:
            filename: Original filename
            content_type: MIME type of the audio
            size: File size in bytes
            duration_ms: Duration of the recording in milliseconds
            waveform: Base64-encoded waveform data for visual display

        Returns:
            Validated voice metadata dict

        Raises:
            ValueError: If validation fails
        """
        # Validate content type
        if not content_type or not content_type.startswith("audio/"):
            raise ValueError("Voice messages must have an audio content type")

        # Validate file extension
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in self.ALLOWED_FORMATS:
            raise ValueError(
                f"Unsupported voice format '{ext}'. Allowed: {', '.join(sorted(self.ALLOWED_FORMATS))}"
            )

        # Validate file size
        if size <= 0:
            raise ValueError("Voice file is empty")
        if size > self.MAX_FILE_SIZE_BYTES:
            max_mb = self.MAX_FILE_SIZE_BYTES // (1024 * 1024)
            raise ValueError(f"Voice file exceeds maximum size of {max_mb}MB")

        # Validate duration if provided
        if duration_ms is not None:
            if duration_ms < self.MIN_VOICE_DURATION_MS:
                raise ValueError("Voice message is too short (minimum 100ms)")
            if duration_ms > self.MAX_VOICE_DURATION_MS:
                max_min = self.MAX_VOICE_DURATION_MS // 60000
                raise ValueError(
                    f"Voice message exceeds maximum duration of {max_min} minutes"
                )

        # Validate waveform if provided (base64, max 10KB)
        if waveform and len(waveform) > 10240:
            raise ValueError("Waveform data exceeds maximum size")

        return {
            "voice_validated": True,
            "format": ext,
            "content_type": content_type,
            "size": size,
            "duration_ms": duration_ms,
            "waveform": waveform,
        }

    def create_voice_message(
        self,
        user_id: int,
        conversation_id: int,
        duration_ms: int,
        filename: str,
        content_type: str,
        size: int,
        url: str,
        waveform: Optional[str] = None,
        transcription: Optional[str] = None,
        participant_svc=None,
    ) -> Dict[str, Any]:
        """
        Create a voice message record and attachment.

        Args:
            user_id: ID of the user sending the voice message
            conversation_id: ID of the target conversation
            duration_ms: Duration of the recording in milliseconds
            filename: Original filename
            content_type: MIME type
            size: File size in bytes
            url: Storage URL for the audio file
            waveform: Optional base64 waveform data
            transcription: Optional auto-transcription text
            participant_svc: Optional participant service for access checks

        Returns:
            Voice message metadata dict

        Raises:
            PermissionError: If user is not a participant in the conversation
        """
        # Verify user is a participant in the target conversation
        if participant_svc and not participant_svc.is_participant(
            conversation_id, user_id
        ):
            raise PermissionError("Not a participant in the target conversation")

        validated = self.validate_voice_attachment(
            filename, content_type, size, duration_ms, waveform
        )

        now = self._get_timestamp()
        voice_id = self._generate_id()

        # Build voice metadata
        voice_metadata = {
            "voice_id": voice_id,
            "duration_ms": duration_ms,
            "waveform": waveform,
            "transcription": transcription,
            "format": validated["format"],
        }

        logger.debug(
            f"Voice message {voice_id} created by user {user_id} in conversation {conversation_id} "
            f"(duration: {duration_ms}ms, format: {validated['format']})"
        )

        return {
            "id": voice_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "duration_ms": duration_ms,
            "filename": filename,
            "content_type": content_type,
            "size": size,
            "url": url,
            "waveform": waveform,
            "transcription": transcription,
            "created_at": now,
            "metadata": voice_metadata,
            # Attachment data for the messaging module to use
            "attachment_data": {
                "filename": filename,
                "content_type": content_type,
                "size": size,
                "url": url,
                "metadata": voice_metadata,
            },
        }

    def update_transcription(self, message_id: int, transcription: str) -> bool:
        """
        Update the transcription for a voice message.

        Called asynchronously after a voice message is sent and
        the transcription service has processed it.
        """
        now = self._get_timestamp()

        # Update the message metadata with transcription
        row = self._db.fetch_one(
            "SELECT metadata FROM msg_messages WHERE id = ? AND deleted = 0",
            (message_id,),
        )
        if not row:
            return False

        try:
            import json

            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            metadata["voice_transcription"] = transcription
            self._db.execute(
                "UPDATE msg_messages SET metadata = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata), now, message_id),
            )
            logger.debug(f"Updated transcription for voice message {message_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update transcription for {message_id}: {e}")
            return False

    def get_voice_metadata(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Extract voice metadata from a message's attachment metadata."""
        row = self._db.fetch_one(
            "SELECT metadata FROM msg_messages WHERE id = ? AND deleted = 0",
            (message_id,),
        )
        if not row or not row["metadata"]:
            return None

        try:
            metadata = json.loads(row["metadata"])
            if metadata.get("voice_id") or metadata.get("duration_ms"):
                return metadata
            return None
        except (json.JSONDecodeError, TypeError):
            return None
