"""
Transcript export service - Chat transcript export functionality.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import json
import csv
import io
import os
import secrets
import tempfile
import threading
import time

from pathlib import Path

import utils.logger as logger
import utils.config as config
from ..repositories.message import MessageRepository
from ..repositories.participant import ParticipantRepository
from .base import BaseService
from src.core.base import SnowflakeID


class TranscriptExportService(BaseService):
    """Service for chat transcript export."""

    # Process-local cache of exports. Records are ALSO persisted as JSON
    # metadata files inside the (0700) storage directory, so status and
    # download requests served by a DIFFERENT worker process still find
    # the export instead of 404-ing (multi-worker amnesia).
    _exports: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()
    _hydrated = False

    @staticmethod
    def _storage_parent() -> Path:
        """Base directory for transcript export artifacts."""
        configured = config.get("transcript_export", {}).get("storage_dir")
        if configured:
            return Path(configured)
        return Path.home() / ".plexichat" / "data" / "transcript_exports"

    @classmethod
    def _records_dir(cls) -> Path:
        return cls._storage_parent() / "records"

    def __init__(
        self,
        db: Any,
        message_repo: MessageRepository,
        participant_repo: ParticipantRepository,
    ) -> None:
        super().__init__(db)
        self._message_repo = message_repo
        self._participant_repo = participant_repo

    def request_export(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        export_format: str = "json",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        timezone: str = "UTC",
    ) -> Dict[str, Any]:
        """Request a transcript export."""
        # Unpredictable export id: timestamp+user_id ids were guessable.
        export_id = f"export_{secrets.token_urlsafe(16)}"
        export_config = config.get("transcript_export", {})

        # Rate limit BEFORE doing any generation work so a rate-limited
        # caller never produces an untracked plaintext file on disk.
        min_interval = export_config.get("rate_limit", {}).get("requests_per_hour", 5)
        self._hydrate()
        with self._lock:
            recent_count = sum(
                1
                for e in self._exports.values()
                if e.get("user_id") == str(user_id)
                and e.get("created_at", 0) > time.time() - 3600
            )
        if recent_count >= min_interval:
            return {
                "export_id": export_id,
                "status": "failed",
                "message_count": 0,
                "file_url": None,
                "expires_at": None,
                "error": f"Rate limit exceeded (max {min_interval} exports per hour)",
            }

        max_messages = export_config.get("max_messages_per_export", 10000)

        file_path: Optional[str] = None
        try:
            messages = self._get_messages_for_export(
                user_id, conversation_id, from_date, to_date, max_messages
            )

            if export_format == "json":
                content, mime = self._generate_json(messages, user_id)
            elif export_format == "csv":
                content, mime = self._generate_csv(messages, user_id)
            elif export_format == "txt":
                content, mime = self._generate_txt(messages, user_id)
            elif export_format == "html":
                content, mime = self._generate_html(messages, user_id)
            else:
                content, mime = self._generate_json(messages, user_id)

            # Store in a private (0700) directory under the app data dir;
            # the shared system tempdir is typically world-readable and
            # these bytes contain users' message contents.
            parent = self._storage_parent()
            parent.mkdir(parents=True, exist_ok=True)
            os.chmod(parent, 0o700)
            session_dir = tempfile.mkdtemp(prefix="gen_", dir=str(parent))
            os.chmod(session_dir, 0o700)
            file_path = os.path.join(session_dir, f"{export_id}.{export_format}")

            if isinstance(content, str):
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                with open(file_path, "wb") as f:
                    f.write(content)
            os.chmod(file_path, 0o600)

            expiry = int(time.time()) + (
                export_config.get("temporary_storage_hours", 24) * 3600
            )

            export_info = {
                "export_id": export_id,
                "user_id": str(user_id),
                "status": "ready",
                "message_count": len(messages),
                "file_path": file_path,
                "file_url": f"/channels/{conversation_id}/messages/export/{export_id}/download",
                "expires_at": expiry,
                "created_at": time.time(),
                "mime_type": mime,
                "format": export_format,
            }
            with self._lock:
                self._exports[export_id] = export_info
            self._persist_record(export_info)
            return export_info

        except Exception as e:
            logger.error(f"Export generation failed: {e}", exc_info=True)
            # Failure path: never leave an orphaned plaintext transcript
            # behind on disk.
            if file_path:
                try:
                    os.remove(file_path)
                    os.rmdir(os.path.dirname(file_path))
                except OSError:
                    pass
            return {
                "export_id": export_id,
                "status": "failed",
                "message_count": 0,
                "file_url": None,
                "expires_at": None,
                "error": str(e),
            }

    def get_export_status(self, export_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of an export."""
        self._cleanup_expired()
        self._hydrate()
        return self._exports.get(export_id)

    def get_export_file_path(self, export_id: str, user_id: str = "") -> Optional[str]:
        """Get the file path for a completed export (with optional ownership check)."""
        self._cleanup_expired()
        self._hydrate()
        info = self._exports.get(export_id)
        if not info or info.get("status") != "ready":
            return None
        if user_id and str(info.get("user_id", "")) != str(user_id):
            return None
        return info.get("file_path")

    @classmethod
    def _persist_record(cls, info: Dict[str, Any]) -> None:
        """Persist an export record so other worker processes can see it."""
        try:
            records_dir = cls._records_dir()
            records_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(cls._storage_parent(), 0o700)
            os.chmod(records_dir, 0o700)
            record_path = records_dir / f"{info['export_id']}.json"
            tmp_path = str(record_path) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(info, f)
            os.replace(tmp_path, record_path)
            os.chmod(record_path, 0o600)
        except Exception as e:
            logger.warning(
                f"Transcript export: failed to persist record "
                f"{info.get('export_id')}: {e}"
            )

    @classmethod
    def _hydrate(cls) -> None:
        """Load persisted export records created by other workers."""
        if cls._hydrated:
            return
        with cls._lock:
            if cls._hydrated:
                return
            try:
                records_dir = cls._records_dir()
                if records_dir.is_dir():
                    now = time.time()
                    for record_file in records_dir.glob("*.json"):
                        try:
                            with open(record_file, encoding="utf-8") as f:
                                info = json.load(f)
                            eid = info.get("export_id")
                            if not eid:
                                continue
                            if info.get("expires_at", 0) < now:
                                record_file.unlink(missing_ok=True)
                                continue
                            cls._exports.setdefault(eid, info)
                        except (OSError, ValueError):
                            continue
            except Exception as e:
                logger.warning(f"Transcript export: record hydration failed: {e}")
            cls._hydrated = True

    def _cleanup_expired(self) -> None:
        """Remove expired exports from memory, records, and disk."""
        self._hydrate()
        now = time.time()
        expired_ids = [
            eid
            for eid, info in self._exports.items()
            if info.get("expires_at", 0) < now
        ]
        for eid in expired_ids:
            with self._lock:
                info = self._exports.pop(eid, None)
            if info and info.get("file_path"):
                try:
                    os.remove(info["file_path"])
                    os.rmdir(os.path.dirname(info["file_path"]))
                except OSError:
                    pass
            try:
                (self._records_dir() / f"{eid}.json").unlink(missing_ok=True)
            except OSError:
                pass

    def _get_messages_for_export(
        self,
        requester_id: SnowflakeID,
        conversation_id: SnowflakeID,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        max_messages: int = 10000,
    ) -> List[Dict[str, Any]]:
        """Get messages for export with optional date filtering.

        SECURITY: validates that the requester is a participant of the
        conversation before dumping ANY message content. Without this a
        guessed/enumerated conversation id yields other users' private
        messages even when the HTTP route's own checks are bypassed.
        """
        if not self._participant_repo.exists(conversation_id, requester_id):
            raise PermissionError(
                f"User {requester_id} is not a participant of "
                f"conversation {conversation_id}"
            )

        messages = self._message_repo.get_by_conversation(
            conversation_id, limit=max_messages
        )

        if not messages:
            return []

        from_ts = None
        to_ts = None

        if from_date:
            try:
                from_ts = int(
                    __import__("datetime").datetime.fromisoformat(from_date).timestamp()
                    * 1000
                )
            except ValueError:
                raise ValueError(f"Invalid from_date format: {from_date}")

        if to_date:
            try:
                to_ts = int(
                    __import__("datetime").datetime.fromisoformat(to_date).timestamp()
                    * 1000
                )
            except ValueError:
                raise ValueError(f"Invalid to_date format: {to_date}")

        result = []
        for msg in messages:
            msg_time = msg.get("created_at", 0)
            if from_ts is not None and msg_time < from_ts:
                continue
            if to_ts is not None and msg_time > to_ts:
                continue
            result.append(msg)

        return result

    def _generate_json(
        self, messages: List[Dict[str, Any]], user_id: SnowflakeID
    ) -> tuple:
        """Generate JSON export."""
        data = []
        for msg in messages:
            data.append(
                {
                    "id": str(msg.get("id", "")),
                    "author_id": str(msg.get("author_id", "")),
                    "content": msg.get("content", ""),
                    "created_at": msg.get("created_at", 0),
                    "edited_at": msg.get("edited_at"),
                    "reply_to_id": str(msg.get("reply_to_id", ""))
                    if msg.get("reply_to_id")
                    else None,
                }
            )
        return json.dumps(data, indent=2, ensure_ascii=False), "application/json"

    def _generate_csv(
        self, messages: List[Dict[str, Any]], user_id: SnowflakeID
    ) -> tuple:
        """Generate CSV export."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["id", "author_id", "content", "created_at", "edited_at", "reply_to_id"]
        )

        for msg in messages:
            writer.writerow(
                [
                    str(msg.get("id", "")),
                    str(msg.get("author_id", "")),
                    msg.get("content", ""),
                    msg.get("created_at", 0),
                    msg.get("edited_at", ""),
                    str(msg.get("reply_to_id", "")) if msg.get("reply_to_id") else "",
                ]
            )

        return output.getvalue(), "text/csv"

    def _generate_txt(
        self, messages: List[Dict[str, Any]], user_id: SnowflakeID
    ) -> tuple:
        """Generate plain text export."""
        lines = []
        lines.append("=== Plexichat Chat Transcript ===")
        lines.append(
            f"Generated: {datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}"
        )
        lines.append(f"Messages: {len(messages)}")
        lines.append("=" * 50)
        lines.append("")

        for msg in messages:
            ts = msg.get("created_at", 0)
            try:
                dt = __import__("datetime").datetime.utcfromtimestamp(ts / 1000)
                time_str = dt.isoformat()
            except Exception:
                time_str = str(ts)
            author = str(msg.get("author_id", "unknown"))
            content = msg.get("content", "") or "[attachment]"
            lines.append(f"[{time_str}] User {author}:")
            lines.append(f"  {content}")
            lines.append("")

        return "\n".join(lines), "text/plain"

    def _generate_html(
        self, messages: List[Dict[str, Any]], user_id: SnowflakeID
    ) -> tuple:
        """Generate HTML export."""
        parts = []
        parts.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
        parts.append("<title>Plexichat Chat Transcript</title>")
        parts.append("<style>")
        parts.append(
            "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #0b0f19; color: #f9fafb; }"
        )
        parts.append(
            "h1 { color: #6366f1; border-bottom: 2px solid #1f2937; padding-bottom: 10px; }"
        )
        parts.append(
            ".message { padding: 10px; margin: 8px 0; border-radius: 8px; background: #111827; border: 1px solid #1f2937; }"
        )
        parts.append(
            ".message .meta { font-size: 0.8em; color: #9ca3af; margin-bottom: 4px; }"
        )
        parts.append(".message .content { color: #f9fafb; }")
        parts.append(".timestamp { color: #6b7280; font-size: 0.8em; }")
        parts.append("</style></head><body>")
        parts.append("<h1>Plexichat Chat Transcript</h1>")
        parts.append(
            f"<p>Messages: {len(messages)} | Generated: {datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}</p>"
        )
        parts.append("<div class='messages'>")

        for msg in messages:
            ts = msg.get("created_at", 0)
            try:
                dt = __import__("datetime").datetime.utcfromtimestamp(ts / 1000)
                time_str = dt.isoformat()
            except Exception:
                time_str = str(ts)
            author = str(msg.get("author_id", "unknown"))
            content = msg.get("content", "") or "[attachment]"
            parts.append("<div class='message'>")
            parts.append(
                f"<div class='meta'>User {author} <span class='timestamp'>{time_str}</span></div>"
            )
            parts.append(
                f"<div class='content'>{__import__('html').escape(content)}</div>"
            )
            parts.append("</div>")

        parts.append("</div></body></html>")
        return "\n".join(parts), "text/html"
