"""Residue-free account erasure.

``auth_users`` is only one of the tables that holds a user's data.
``AccountReaper.purge_user`` deletes the row and scrubs a handful of domains,
but three kinds of rows kept their own life:

* servers the user owned (with their channels, conversations, messages and
  audit trail),
* conversations in which the user was the only participant (personal notes,
  thread containers, self-DMs),
* uploaded media: the ``media_files`` rows, the deduplicated
  ``media_file_hashes`` bookkeeping, and the objects in object storage.

Deleting the account alone left all of that behind, owned by a user id that no
longer exists -- nobody could administer or erase it. These helpers are the one
implementation of that cleanup, shared by the reaper (immediate purge), the
self-test cleanup service (test deployments must not accumulate residue) and
the admin force-purge path that drives the reaper.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import utils.logger as logger


# Tables that only exist when an optional domain is installed. A missing table
# is a schema older than the domain, not an error in the purge.
_OPTIONAL_TABLES = (
    "automod_rules",
    "automod_violations",
    "automod_audit",
    "automod_reputation",
    "automod_exemptions",
    "automod_rate_tracking",
    "thread_threads",
    "thread_members",
    "thread_messages",
    "webhook_webhooks",
    "webhook_messages",
)


def _execute(db: Any, statement: str, params: tuple = ()) -> None:
    """Run one cleanup statement, tolerating a schema without that table."""
    try:
        db.execute(statement, params)
    except Exception as exc:
        if any(table in statement for table in _OPTIONAL_TABLES):
            logger.debug(
                "Erase: optional cleanup skipped (%s): %s", statement[:60], exc
            )
            return
        raise


def _rows(db: Any, statement: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in db.fetch_all(statement, params) or []]


def _count(db: Any, statement: str, params: tuple = ()) -> int:
    row = db.fetch_one(statement, params)
    if not row:
        return 0
    value = row["n"] if isinstance(row, dict) else row[0]
    return int(value or 0)


# === servers ===


def purge_server_recursive(db: Any, server_id: int) -> None:
    """Delete a server and everything that hangs off it.

    Channels, messages, threads, webhooks, automod state, roles, members and
    the conversations behind the server's channels are removed before the
    server row itself. Shared with the self-test cleanup, which relied on this
    ordering to keep the server's conversations from surviving as orphans.
    """
    sid = int(server_id)
    _execute(
        db,
        "DELETE FROM srv_member_roles WHERE member_id IN "
        "(SELECT id FROM srv_members WHERE server_id = ?)",
        (sid,),
    )
    _execute(db, "DELETE FROM srv_members WHERE server_id = ?", (sid,))
    _execute(
        db,
        "DELETE FROM srv_channel_overrides WHERE channel_id IN "
        "(SELECT id FROM srv_channels WHERE server_id = ?)",
        (sid,),
    )
    for table in (
        "srv_invites",
        "srv_bans",
        "srv_categories",
        "srv_audit_log",
        "srv_scheduled_events",
        "srv_welcome_screens",
        "srv_onboarding_steps",
        "srv_onboarding_progress",
    ):
        _execute(db, f"DELETE FROM {table} WHERE server_id = ?", (sid,))
    _execute(db, "DELETE FROM srv_templates WHERE source_server_id = ?", (sid,))

    for row in _rows(
        db,
        "SELECT conversation_id FROM srv_channels "
        "WHERE server_id = ? AND conversation_id IS NOT NULL",
        (sid,),
    ):
        delete_conversation(db, row["conversation_id"])

    _execute(db, "DELETE FROM srv_channels WHERE server_id = ?", (sid,))
    _execute(db, "DELETE FROM srv_roles WHERE server_id = ?", (sid,))
    _execute(
        db,
        "DELETE FROM webhook_messages WHERE webhook_id IN "
        "(SELECT id FROM webhook_webhooks WHERE server_id = ?)",
        (sid,),
    )
    _execute(db, "DELETE FROM webhook_webhooks WHERE server_id = ?", (sid,))
    for table in (
        "automod_rules",
        "automod_violations",
        "automod_audit",
        "automod_reputation",
        "automod_exemptions",
        "automod_rate_tracking",
    ):
        _execute(db, f"DELETE FROM {table} WHERE server_id = ?", (sid,))
    _execute(db, "DELETE FROM srv_servers WHERE id = ?", (sid,))


def purge_owned_servers(db: Any, user_id: int) -> List[int]:
    """Delete every server the user owns and return the ids that went."""
    ids = [
        int(row["id"])
        for row in _rows(
            db, "SELECT id FROM srv_servers WHERE owner_id = ?", (user_id,)
        )
    ]
    for sid in ids:
        purge_server_recursive(db, sid)
    return ids


# === conversations ===


def delete_conversation(db: Any, conversation_id: int) -> None:
    """Delete a conversation with its messages, attachments and threads."""
    cid = int(conversation_id)
    _execute(
        db,
        "DELETE FROM msg_message_status WHERE message_id IN "
        "(SELECT id FROM msg_messages WHERE conversation_id = ?)",
        (cid,),
    )
    _execute(db, "DELETE FROM msg_pinned WHERE conversation_id = ?", (cid,))
    _execute(
        db,
        "DELETE FROM msg_attachments WHERE message_id IN "
        "(SELECT id FROM msg_messages WHERE conversation_id = ?)",
        (cid,),
    )
    _execute(
        db,
        "DELETE FROM thread_messages WHERE thread_id IN "
        "(SELECT id FROM thread_threads WHERE conversation_id = ?)",
        (cid,),
    )
    _execute(
        db,
        "DELETE FROM thread_members WHERE thread_id IN "
        "(SELECT id FROM thread_threads WHERE conversation_id = ?)",
        (cid,),
    )
    _execute(db, "DELETE FROM thread_threads WHERE conversation_id = ?", (cid,))
    _execute(db, "DELETE FROM msg_messages WHERE conversation_id = ?", (cid,))
    _execute(db, "DELETE FROM msg_participants WHERE conversation_id = ?", (cid,))
    _execute(db, "DELETE FROM msg_conversations WHERE id = ?", (cid,))


def has_other_participants(db: Any, conversation_id: int, user_id: int) -> bool:
    return (
        _count(
            db,
            "SELECT count(*) AS n FROM msg_participants "
            "WHERE conversation_id = ? AND user_id != ?",
            (int(conversation_id), int(user_id)),
        )
        > 0
    )


def purge_user_conversations(db: Any, user_id: int) -> List[int]:
    """Detach a user from conversations, deleting the ones left empty.

    A DM the user shared with somebody who is still here keeps its messages
    (already anonymised by the purge) and the surviving participant's copy; a
    conversation nobody is left in -- personal notes, thread containers, a
    self-DM -- is erased with everything in it.
    """
    uid = int(user_id)
    conversation_ids = [
        int(row["conversation_id"])
        for row in _rows(
            db, "SELECT conversation_id FROM msg_participants WHERE user_id = ?", (uid,)
        )
    ]
    owned_threads = _owned_thread_ids(db, uid)
    # Thread containers have no ``msg_participants`` rows -- access runs through
    # ``thread_members`` -- so they never appear in ``conversation_ids`` and
    # have to be collected from the threads themselves.
    owned_thread_conversations = _owned_thread_conversation_ids(db, uid)
    _execute(db, "DELETE FROM msg_participants WHERE user_id = ?", (uid,))
    _execute(db, "DELETE FROM thread_members WHERE user_id = ?", (uid,))
    for thread_id in owned_threads:
        _execute(db, "DELETE FROM thread_messages WHERE thread_id = ?", (thread_id,))
        _execute(db, "DELETE FROM thread_members WHERE thread_id = ?", (thread_id,))
        _execute(db, "DELETE FROM thread_threads WHERE id = ?", (thread_id,))

    removed: List[int] = []
    for cid in owned_thread_conversations:
        delete_conversation(db, cid)
        removed.append(cid)
    for cid in conversation_ids:
        if cid in removed or has_other_participants(db, cid, uid):
            continue
        delete_conversation(db, cid)
        removed.append(cid)
    return removed


def _owned_thread_ids(db: Any, user_id: int) -> List[int]:
    try:
        return [
            int(row["id"])
            for row in _rows(
                db, "SELECT id FROM thread_threads WHERE owner_id = ?", (user_id,)
            )
        ]
    except Exception:
        return []


def _owned_thread_conversation_ids(db: Any, user_id: int) -> List[int]:
    """Conversation ids of the thread containers this user started."""
    try:
        return [
            int(row["conversation_id"])
            for row in _rows(
                db,
                "SELECT conversation_id FROM thread_threads "
                "WHERE owner_id = ? AND conversation_id IS NOT NULL",
                (int(user_id),),
            )
        ]
    except Exception:
        return []


_ORPHANED_CONVERSATIONS = (
    "SELECT c.id FROM msg_conversations c WHERE NOT EXISTS "
    "(SELECT 1 FROM msg_participants p WHERE p.conversation_id = c.id) "
    "AND NOT EXISTS "
    "(SELECT 1 FROM thread_threads t WHERE t.conversation_id = c.id)"
)

# Thread tables are optional; without them nothing has to be excluded.
_ORPHANED_CONVERSATIONS_NO_THREADS = (
    "SELECT c.id FROM msg_conversations c WHERE NOT EXISTS "
    "(SELECT 1 FROM msg_participants p WHERE p.conversation_id = c.id)"
)


def purge_orphaned_conversations(db: Any) -> List[int]:
    """Delete conversations that no user participates in any more.

    A thread's conversation is created without ``msg_participants`` rows -- the
    thread's access is tracked in ``thread_members`` -- so a live thread looks
    "empty" to a participant count. Conversations a thread still points at are
    therefore left alone; a container whose thread row is gone is swept like
    any other orphan.
    """
    try:
        rows = _rows(db, _ORPHANED_CONVERSATIONS)
    except Exception:
        rows = _rows(db, _ORPHANED_CONVERSATIONS_NO_THREADS)
    ids = [int(row["id"]) for row in rows]
    for cid in ids:
        delete_conversation(db, cid)
    return ids


# === media ===


def _media_manager() -> Optional[Any]:
    """The live media module, or None in a context that never set it up."""
    try:
        import src.api as api

        return api.get_media()
    except Exception:
        return None


def purge_user_media(db: Any, user_id: int, media: Optional[Any] = None) -> List[str]:
    """Erase a user's uploads, returning storage paths that could not go.

    Prefers the media domain, which knows about deduplication: a file whose
    content is shared with another user's upload cannot have its object
    removed, but this user's metadata rows and reference still go. Without the
    domain (older installs, tests) the rows are removed and the object paths
    are reported for an operator to sweep, mirroring the DSAR purge.
    """
    uid = int(user_id)
    manager = media if media is not None else _media_manager()
    if manager is not None:
        try:
            return list(manager.delete_files_for_user(uid) or [])
        except Exception as exc:
            logger.warning("Erase: media purge via the media domain failed: %s", exc)

    rows = _rows(
        db,
        "SELECT id, storage_backend, storage_path FROM media_files "
        "WHERE uploaded_by = ?",
        (uid,),
    )
    unswept = []
    for row in rows:
        path = row["storage_path"]
        if path:
            unswept.append(str(path))
        _execute(
            db,
            "DELETE FROM media_file_deletion_locks WHERE media_file_id = ?",
            (int(row["id"]),),
        )
        _execute(
            db,
            "DELETE FROM media_file_deletion_tombstones WHERE media_file_id = ?",
            (int(row["id"]),),
        )
        _execute(db, "DELETE FROM media_files WHERE id = ?", (int(row["id"]),))
    return unswept


# === orchestration ===


def erase_user_residue(
    db: Any, user_id: int, media: Optional[Any] = None
) -> Dict[str, Any]:
    """Erase everything the account owned outside the ``auth_users`` row."""
    servers = purge_owned_servers(db, user_id)
    conversations = purge_user_conversations(db, user_id)
    unswept_media = purge_user_media(db, user_id, media=media)
    if servers or conversations or unswept_media:
        logger.info(
            "Erase: user %s left %s server(s), %s conversation(s), %s unswept "
            "media object(s) cleaned up",
            user_id,
            len(servers),
            len(conversations),
            len(unswept_media),
        )
    return {
        "servers": servers,
        "conversations": conversations,
        "media_unswept": unswept_media,
    }


def sweep_orphaned_servers(db: Any) -> List[int]:
    """Delete servers whose owning account no longer exists."""
    ids = [
        int(row["id"])
        for row in _rows(
            db,
            "SELECT s.id FROM srv_servers s LEFT JOIN auth_users u "
            "ON u.id = s.owner_id WHERE u.id IS NULL",
        )
    ]
    for sid in ids:
        purge_server_recursive(db, sid)
    return ids


def sweep_orphans(db: Any, media: Optional[Any] = None) -> Dict[str, List]:
    """Remove residue left by purges that predate the cascade.

    Every purge now cascades, but a deployment can still carry rows from an
    earlier release, and a crash mid-purge leaves a partial one. The self-test
    runs this before its own cleanup so a test deployment cannot accumulate
    data for accounts that do not exist.
    """
    orphan_uploaders = [
        int(row["uploaded_by"])
        for row in _rows(
            db,
            "SELECT DISTINCT m.uploaded_by FROM media_files m "
            "LEFT JOIN auth_users u ON u.id = m.uploaded_by WHERE u.id IS NULL",
        )
    ]
    unswept: List[str] = []
    for uploader in orphan_uploaders:
        unswept.extend(purge_user_media(db, uploader, media=media))

    result = {
        "servers": sweep_orphaned_servers(db),
        "conversations": purge_orphaned_conversations(db),
        "media_unswept": unswept,
    }
    if result["servers"] or result["conversations"] or unswept:
        logger.info(
            "Erase: swept %s orphan server(s), %s orphan conversation(s), "
            "%s unswept media object(s)",
            len(result["servers"]),
            len(result["conversations"]),
            len(unswept),
        )
    return result


__all__ = [
    "delete_conversation",
    "erase_user_residue",
    "has_other_participants",
    "purge_orphaned_conversations",
    "purge_owned_servers",
    "purge_server_recursive",
    "purge_user_conversations",
    "purge_user_media",
    "sweep_orphaned_servers",
    "sweep_orphans",
]
