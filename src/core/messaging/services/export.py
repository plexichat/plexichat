"""
Transcript export service - Chat transcript export functionality.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import json
import csv
import html
import io
import os
import secrets
import tempfile
import threading
import time

from pathlib import Path
import re

import utils.logger as logger
import utils.config as config
from ..repositories.message import MessageRepository
from ..repositories.participant import ParticipantRepository
from .base import BaseService
from src.core.base import SnowflakeID

# Server-generated export IDs are always ``export_`` + base64url entropy.
# The getters reject anything else so a crafted ``export_id`` can never
# drive path construction (traversal) or probe bookkeeping.
_EXPORT_ID_RE = re.compile(r"^export_[A-Za-z0-9_-]{16,}$")


def _resolve_date_zone(tz_name: Optional[str]):
    """Resolve an IANA timezone name for export date bounds.

    Falls back to UTC when the name is absent or not recognised, so an
    unusable timezone can never silently reintroduce server-local
    interpretation of the date bounds.
    """
    if not tz_name or tz_name.upper() == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        logger.warning(f"Transcript export: unknown timezone '{tz_name}'; using UTC")
        return timezone.utc


def _parse_iso_date_bound(value: str, tz_name: Optional[str]) -> int:
    """Parse an ISO-8601 date/datetime into epoch milliseconds.

    A value carrying no explicit UTC offset is interpreted in ``tz_name``
    (default UTC), matching the request's documented "timezone for dates"
    contract and the UTC-millis storage of ``created_at``. The previous
    implementation called ``.timestamp()`` on the naive value, which the
    platform resolves in the SERVER's local zone -- so the same request
    filtered differently depending on the host's TZ.

    Raises ``ValueError`` for an unparseable value.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_resolve_date_zone(tz_name))
    return int(parsed.timestamp() * 1000)


class TranscriptExportService(BaseService):
    """Service for chat transcript export."""

    # Process-local cache of exports. Records are ALSO persisted as JSON
    # metadata files inside the (0700) storage directory, so status and
    # download requests served by a DIFFERENT worker process still find
    # the export instead of 404-ing (multi-worker amnesia).
    _exports: Dict[str, Dict[str, Any]] = {}
    # RLock: ID allocation re-checks membership while the caller already
    # holds the lock so generation + rate-check + placeholder reservation
    # commit atomically in one critical section (no TOCTOU).
    _lock = threading.RLock()
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

    @classmethod
    def _new_export_id(cls) -> str:
        """Generate a collision-free unpredictable export id (256-bit).

        The membership re-check runs under the lock; callers that already
        hold ``_lock`` (request_export's atomic reserve) stay correct
        because the lock is re-entrant, and the placeholder insert below
        lands before the outer critical section is released.
        """
        for _ in range(10):
            candidate = f"export_{secrets.token_urlsafe(32)}"
            with cls._lock:
                if candidate not in cls._exports:
                    return candidate
        # 10 consecutive 256-bit collisions is effectively impossible;
        # fail closed rather than falling back to anything predictable.
        raise RuntimeError("export id allocation failed: repeated collision")

    @staticmethod
    def _is_valid_export_id(export_id: Any) -> bool:
        """True only for server-shaped export IDs (traversal-safe)."""
        return isinstance(export_id, str) and bool(_EXPORT_ID_RE.match(export_id))

    @staticmethod
    def _ensure_private_dir(path: Path) -> None:
        """Create a directory with 0700 perms (chmod fixes pre-existing)."""
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass

    @staticmethod
    def _write_private_file(path: str, content: Any) -> None:
        """Write a file atomically with 0600 perms (no world-readable window).

        ``open()`` + ``chmod`` leaves the bytes world-readable under a
        permissive umask until the chmod lands; ``os.open`` with an explicit
        mode applies the restriction at creation time instead.
        O_EXCL fails closed on collision; O_NOFOLLOW refuses to follow a
        pre-planted symlink at the target path.
        """
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            if isinstance(content, str):
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                fd = -1
            else:
                with os.fdopen(fd, "wb") as f:
                    f.write(content)
                fd = -1
        finally:
            if fd != -1:
                try:
                    os.close(fd)
                except OSError:
                    pass
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

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
        export_config = config.get("transcript_export", {})
        retention_hours = export_config.get("temporary_storage_hours", 24)

        file_path: Optional[str] = None
        export_id: Optional[str] = None
        now = time.time()
        try:
            # AUTHORIZATION FIRST: a non-participant must never consume
            # rate-limit budget nor produce any artifact on disk, even when
            # the HTTP route's own checks are bypassed.
            if not self._participant_repo.exists(conversation_id, user_id):
                raise PermissionError(
                    f"User {user_id} is not a participant of "
                    f"conversation {conversation_id}"
                )

            # RATE LIMIT next, enforced BEFORE any generation work or file
            # creation so a rate-limited caller never produces an untracked
            # plaintext file on disk. ID allocation, the count check, and
            # the pending placeholder reservation all commit under ONE
            # lock hold, so concurrent bursts cannot each pass the check
            # before the others are recorded and cannot pick the same ID
            # between generation and reservation (TOCTOU closed).
            min_interval = export_config.get("rate_limit", {}).get(
                "requests_per_hour", 5
            )
            self._hydrate()
            with self._lock:
                recent_count = sum(
                    1
                    for e in self._exports.values()
                    if e.get("user_id") == str(user_id)
                    and (e.get("created_at") or 0) > now - 3600
                )
                if recent_count >= min_interval:
                    rate_limited = True
                    # Untracked ID for the response only: never inserted,
                    # so a limited caller consumes no slot and leaves no
                    # entry behind.
                    export_id = self._new_export_id()
                else:
                    rate_limited = False
                    export_id = self._new_export_id()
                    self._exports[export_id] = {
                        "export_id": export_id,
                        "user_id": str(user_id),
                        "status": "pending",
                        "created_at": now,
                        "expires_at": int(now) + retention_hours * 3600,
                    }
            if rate_limited:
                return {
                    "export_id": export_id,
                    "status": "failed",
                    "message_count": 0,
                    "file_url": None,
                    "expires_at": None,
                    "error": f"Rate limit exceeded (max {min_interval} exports per hour)",
                }

            max_messages = export_config.get("max_messages_per_export", 10000)

            messages = self._get_messages_for_export(
                user_id,
                conversation_id,
                from_date,
                to_date,
                max_messages,
                timezone,
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
            self._ensure_private_dir(parent)
            session_dir = tempfile.mkdtemp(prefix="gen_", dir=str(parent))
            try:
                os.chmod(session_dir, 0o700)
            except OSError:
                pass
            file_path = os.path.join(session_dir, f"{export_id}.{export_format}")

            self._write_private_file(file_path, content)

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
            # behind on disk. The placeholder is replaced with a terminal
            # failed record under lock so concurrent bookkeeping never
            # loses or corrupts the entry.
            if file_path:
                try:
                    os.remove(file_path)
                    os.rmdir(os.path.dirname(file_path))
                except OSError:
                    pass
            if isinstance(e, PermissionError):
                # Authorization failure leaves NOTHING behind: no file
                # (none was created), no bookkeeping entry, no rate-limit
                # budget consumed. The response carries an untracked ID so
                # the caller learns nothing enumerable.
                return {
                    "export_id": export_id or f"export_{secrets.token_urlsafe(32)}",
                    "status": "failed",
                    "message_count": 0,
                    "file_url": None,
                    "expires_at": None,
                    "error": str(e),
                }
            if export_id is None:
                # Allocation itself failed (collision storm): nothing was
                # reserved, so report without inserting.
                return {
                    "export_id": f"export_{secrets.token_urlsafe(32)}",
                    "status": "failed",
                    "message_count": 0,
                    "file_url": None,
                    "expires_at": None,
                    "error": str(e),
                }
            failed_at = time.time()
            with self._lock:
                self._exports[export_id] = {
                    "export_id": export_id,
                    "user_id": str(user_id),
                    "status": "failed",
                    "message_count": 0,
                    "file_url": None,
                    "expires_at": int(failed_at) + 3600,
                    "created_at": now,
                    "error": str(e),
                }
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
        if not self._is_valid_export_id(export_id):
            return None
        self._cleanup_expired()
        self._hydrate()
        with self._lock:
            info = self._exports.get(export_id)
            return dict(info) if info is not None else None

    def get_export_file_path(self, export_id: str, user_id: str = "") -> Optional[str]:
        """Get the file path for a completed export (with optional ownership check)."""
        if not self._is_valid_export_id(export_id):
            return None
        self._cleanup_expired()
        self._hydrate()
        with self._lock:
            info = self._exports.get(export_id)
            if not info or info.get("status") != "ready":
                return None
            if user_id and str(info.get("user_id", "")) != str(user_id):
                return None
            return info.get("file_path")

    @classmethod
    def _persist_record(cls, info: Dict[str, Any]) -> None:
        """Persist an export record so other worker processes can see it.

        The record is written to a uniquely-named temp file created with
        O_EXCL 0600 (``tempfile.mkstemp``) in the same directory and then
        atomically renamed over the target: no predictable ``.tmp`` path,
        no truncate-follows-symlink window, no world-readable bytes.
        """
        try:
            if not cls._is_valid_export_id(info.get("export_id")):
                return
            records_dir = cls._records_dir()
            cls._ensure_private_dir(records_dir)
            try:
                os.chmod(cls._storage_parent(), 0o700)
            except OSError:
                pass
            record_path = records_dir / f"{info['export_id']}.json"
            payload = json.dumps(info)
            # mkstemp: O_CREAT|O_EXCL|O_NOFOLLOW, mode 0600 at creation.
            fd, tmp_path = tempfile.mkstemp(dir=str(records_dir), prefix=".tmp-")
            try:
                try:
                    os.chmod(tmp_path, 0o600)
                except OSError:
                    pass
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp_path, record_path)
                try:
                    os.chmod(record_path, 0o600)
                except OSError:
                    pass
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
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
                    # Best-effort sweep of stale mkstemp leftovers from
                    # crashed workers (never matched by the *.json glob).
                    for stale in records_dir.glob(".tmp-*"):
                        try:
                            if now - stale.stat().st_mtime > 3600:
                                stale.unlink(missing_ok=True)
                        except OSError:
                            continue
                    for record_file in records_dir.glob("*.json"):
                        try:
                            with open(record_file, encoding="utf-8") as f:
                                info = json.load(f)
                            eid = info.get("export_id")
                            if not eid:
                                continue
                            if (info.get("expires_at") or 0) < now:
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
        # Snapshot + pop under lock: iterating the live dict without the
        # lock races with concurrent request_export inserts and raises
        # "dictionary changed size during iteration".
        with self._lock:
            expired_ids = [
                eid
                for eid, info in self._exports.items()
                if (info.get("expires_at") or 0) < now
            ]
            expired_infos = {eid: self._exports.pop(eid, None) for eid in expired_ids}
        for eid in expired_ids:
            info = expired_infos.get(eid)
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
        tz_name: Optional[str] = "UTC",
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

        from_ts = None
        to_ts = None

        if from_date:
            try:
                from_ts = _parse_iso_date_bound(from_date, tz_name)
            except ValueError:
                raise ValueError(f"Invalid from_date format: {from_date}")

        if to_date:
            try:
                to_ts = _parse_iso_date_bound(to_date, tz_name)
            except ValueError:
                raise ValueError(f"Invalid to_date format: {to_date}")

        # Apply the date bounds in SQL rather than filtering a capped fetch.
        # Fetching ``limit`` newest rows (``ORDER BY id DESC``) *before*
        # filtering meant an older date window returned nothing once a
        # conversation exceeded ``max_messages``; ASC ordering also yields a
        # chronological transcript.
        messages = self._message_repo.get_by_conversation(
            conversation_id,
            limit=max_messages,
            from_ts=from_ts,
            to_ts=to_ts,
            ascending=True,
        )

        if not messages:
            return []

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
        lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
        lines.append(f"Messages: {len(messages)}")
        lines.append("=" * 50)
        lines.append("")

        for msg in messages:
            ts = msg.get("created_at", 0)
            try:
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
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
            f"<p>Messages: {len(messages)} | Generated: {datetime.now(timezone.utc).isoformat()}</p>"
        )
        parts.append("<div class='messages'>")

        for msg in messages:
            ts = msg.get("created_at", 0)
            try:
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                time_str = dt.isoformat()
            except Exception:
                time_str = str(ts)
            author = str(msg.get("author_id", "unknown"))
            content = msg.get("content", "") or "[attachment]"
            parts.append("<div class='message'>")
            parts.append(
                f"<div class='meta'>User {author} <span class='timestamp'>{time_str}</span></div>"
            )
            parts.append(f"<div class='content'>{html.escape(content)}</div>")
            parts.append("</div>")

        parts.append("</div></body></html>")
        return "\n".join(parts), "text/html"
