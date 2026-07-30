# pyright: reportAttributeAccessIssue=false
"""
Malware-scanning methods mixed into MediaManager.
"""

import logging
from typing import Optional, Tuple

from .models import ScanStatus
from .exceptions import MediaError
from .security import MalwareScanner

logger = logging.getLogger(__name__)


class _ScanningMixin:
    """Malware-scanning methods mixed into MediaManager."""

    def _init_scanner(self) -> MalwareScanner:
        return MalwareScanner(
            host=self._config.get("scanner_host", "localhost"),
            port=self._config.get("scanner_port", 3310),
            enabled=self._config.get("scanner_enabled", False),
        )

    def scan_file(self, file_id: int) -> Tuple[ScanStatus, Optional[str]]:
        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")
        if not self._scanner:
            return ScanStatus.SKIPPED, None
        file_data = self._storage.retrieve(file.storage_path)
        status, result = self._scanner.scan_bytes(file_data)
        self._db.execute(
            "UPDATE media_files SET scan_status = ?, scan_result = ? WHERE id = ?",
            (status.value, result, file_id),
        )
        return status, result

    def _background_scan(
        self,
        file_id: int,
        file_data: bytes,
        user_id: int,
        channel_id: Optional[int] = None,
    ) -> None:
        """Background malware scan — runs in executor after upload returns.

        Updates scan_status in DB. If INFECTED: soft-deletes the file,
        removes the attachment from any referencing messages, and broadcasts
        a WSS MESSAGE_UPDATE so clients drop the attachment.
        """
        try:
            if not self._scanner or not self._scanner.is_available():
                self._db.execute(
                    "UPDATE media_files SET scan_status = ? WHERE id = ?",
                    (ScanStatus.SKIPPED.value, file_id),
                )
                return

            status, result = self._scanner.scan_bytes(file_data)
            self._db.execute(
                "UPDATE media_files SET scan_status = ?, scan_result = ? WHERE id = ?",
                (status.value, result, file_id),
            )

            if status == ScanStatus.INFECTED:
                logger.warning(f"Malware detected in file {file_id}: {result}")
                self._handle_infected_file(file_id, user_id, channel_id)
            else:
                logger.debug(f"Scan complete for file {file_id}: {status.value}")

        except Exception as e:
            logger.error(f"Background scan failed for file {file_id}: {e}")
            try:
                self._db.execute(
                    "UPDATE media_files SET scan_status = ?, scan_result = ? WHERE id = ?",
                    (ScanStatus.ERROR.value, str(e), file_id),
                )
            except Exception:
                pass

    def _handle_infected_file(
        self,
        file_id: int,
        user_id: int,
        channel_id: Optional[int] = None,
    ) -> None:
        """Handle an infected file: soft-delete, remove from messages, notify WSS."""
        try:
            # 1. Soft-delete the media file
            self._db.execute(
                "UPDATE media_files SET deleted = 1, deleted_at = ? WHERE id = ?",
                (self._get_timestamp(), file_id),
            )

            # 2. Find all referencing attachments and soft-delete them
            stored_name_row = self._db.fetch_one(
                "SELECT filename FROM media_files WHERE id = ?", (file_id,)
            )
            if not stored_name_row:
                return
            stored_name = stored_name_row["filename"]

            # Search msg_attachments for references to this file via metadata or URL
            attachments = self._db.fetch_all(
                """SELECT id, message_id, metadata FROM msg_attachments
                   WHERE deleted = 0 AND (url LIKE ? OR metadata LIKE ?)""",
                (f"%{stored_name}%", f"%{file_id}%"),
            )

            affected_message_ids = set()
            for att in attachments:
                att_id = att["id"]
                msg_id = att["message_id"]
                # Soft-delete the attachment
                self._db.execute(
                    "UPDATE msg_attachments SET deleted = 1 WHERE id = ?",
                    (att_id,),
                )
                affected_message_ids.add(msg_id)

            # 3. For each affected message, broadcast a WSS MESSAGE_UPDATE
            #    so clients re-render without the infected attachment.
            if affected_message_ids:
                self._broadcast_scan_infected(affected_message_ids, channel_id)

        except Exception as e:
            logger.error(f"Failed to handle infected file {file_id}: {e}")

    def _broadcast_scan_infected(
        self, message_ids: set, channel_id: Optional[int] = None
    ) -> None:
        """Broadcast MESSAGE_UPDATE for messages that had infected attachments removed."""
        try:
            import asyncio

            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if not ws_is_setup():
                return

            dispatcher = get_dispatcher()

            async def _broadcast():
                # Resolve user_ids from channel_id
                user_ids = []
                if channel_id:
                    try:
                        import src.api as api_module

                        servers_mod = api_module.get_servers()
                        if servers_mod:
                            channel = servers_mod.get_channel(channel_id, 0)
                            if channel:
                                server_id = getattr(channel, "server_id", None)
                                if server_id:
                                    user_ids = servers_mod.get_member_user_ids(
                                        server_id
                                    )
                        if not user_ids:
                            messaging_mod = api_module.get_messaging()
                            if messaging_mod:
                                participants = messaging_mod.get_participants(
                                    0, channel_id
                                )
                                user_ids = [p.user_id for p in (participants or [])]
                    except Exception as e:
                        logger.warning(
                            f"Failed to resolve user_ids for channel {channel_id}: {e}"
                        )

                if not user_ids:
                    return

                for msg_id in message_ids:
                    try:
                        # Fetch updated message data
                        row = self._db.fetch_one(
                            "SELECT * FROM msg_messages WHERE id = ?",
                            (msg_id,),
                        )
                        if not row:
                            continue

                        # Fetch remaining attachments
                        atts = self._db.fetch_all(
                            "SELECT * FROM msg_attachments WHERE message_id = ? AND deleted = 0",
                            (msg_id,),
                        )

                        event_data = {
                            "id": str(msg_id),
                            "content": row.get("content", ""),
                            "attachments": [
                                {
                                    "id": str(a["id"]),
                                    "filename": a["filename"],
                                    "content_type": a["content_type"],
                                    "size": a["size"],
                                    "url": a["url"],
                                }
                                for a in atts
                            ],
                        }

                        event = Event(
                            event_type=EventType.MESSAGE_UPDATE,
                            data=event_data,
                            channel_id=channel_id,
                        )
                        await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.warning(
                            f"Failed to broadcast scan-infected for msg {msg_id}: {e}"
                        )

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_broadcast())
            except RuntimeError:
                asyncio.run(_broadcast())

        except Exception as e:
            logger.warning(f"Failed to broadcast scan-infected events: {e}")
