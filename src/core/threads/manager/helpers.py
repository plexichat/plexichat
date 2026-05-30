from typing import Optional, Dict, Any

import utils.logger as logger
from ..models import Thread, ThreadMember, ThreadState, ThreadType, AutoArchiveDuration


def _row_to_thread(row: Dict[str, Any], encrypt_thread_names: bool = False) -> Thread:
    name = row["name"]
    if encrypt_thread_names and row.get("name_encrypted"):
        from src.utils.encryption import decrypt_data

        try:
            name = decrypt_data(row["name_encrypted"])
        except Exception as e:
            logger.warning(f"Failed to decrypt thread name {row['id']}: {e}")
            name = row["name"]

    return Thread(
        id=row["id"],
        channel_id=row["channel_id"],
        server_id=row["server_id"],
        owner_id=row["owner_id"],
        name=name,
        thread_type=ThreadType(row["thread_type"]),
        state=ThreadState(row["state"]),
        parent_message_id=row["parent_message_id"],
        auto_archive_duration=AutoArchiveDuration(row["auto_archive_duration"]),
        message_count=row["message_count"],
        member_count=row["member_count"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
        last_message_at=row["last_message_at"],
        conversation_id=row.get("conversation_id"),
        locked=bool(row["locked"]),
    )


def _row_to_thread_member(row: Dict[str, Any]) -> ThreadMember:
    return ThreadMember(
        thread_id=row["thread_id"],
        user_id=row["user_id"],
        joined_at=row["joined_at"],
        last_read_message_id=row["last_read_message_id"],
        muted=bool(row["muted"]),
    )


def _get_channel(db, channel_id: int) -> Optional[Dict[str, Any]]:
    row = db.fetch_one(
        "SELECT * FROM srv_channels WHERE id = ? AND deleted = 0", (channel_id,)
    )
    return dict(row) if row else None


def _get_message(db, message_id: int) -> Optional[Dict[str, Any]]:
    row = db.fetch_one(
        "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
    )
    return dict(row) if row else None


def _get_thread_internal(
    db, thread_id: int, encrypt_thread_names: bool = False
) -> Optional[Thread]:
    row = db.fetch_one(
        "SELECT * FROM thread_threads WHERE id = ? AND deleted = 0", (thread_id,)
    )
    return _row_to_thread(row, encrypt_thread_names) if row else None


def _check_auto_archive(thread: Thread, now: int) -> bool:
    if thread.state != ThreadState.ACTIVE:
        return False
    last_activity = thread.last_message_at or thread.created_at
    duration_ms = thread.auto_archive_duration.value * 60 * 1000
    return (now - last_activity) > duration_ms
