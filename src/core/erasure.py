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
from src.core.security.hardening import require_identifier


# Marker written over message content that survives erasure. Keep it identical
# to the reaper's anonymization text so a direct ``erase_user_residue`` call and
# a full ``AccountReaper.purge_user`` render the same tombstone.
ANONYMIZED_CONTENT = "[This message was sent by a deleted user]"
REDACTED_HISTORY_CONTENT = "[redacted]"


# Tables that only exist when an optional domain is installed, or that were
# added after the first schema release. A missing table is an older/partial
# install, not an error in the purge.
_OPTIONAL_TABLES = (
    # Blind-index children of msg_messages. They cascade on PostgreSQL, but
    # SQLite purges run with foreign keys off, so the erasure must delete them
    # explicitly or they survive as keyed-hash residue keyed by a dead author.
    "msg_message_tokens",
    "msg_edit_history",
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

# Per-user residue lives in tables that have no cascade from ``auth_users``
# (some have no foreign key at all). Older or partial installs may not have
# them yet, so a missing table must not abort the rest of the purge.
_RESIDUE_TABLES = (
    "notif_notifications",
    "notif_settings",
    "notif_channel_overrides",
    "notif_unread",
    "pres_presence",
    "pres_custom_status",
    "pres_activity",
    "pres_typing",
    "user_profiles",
    "push_tokens",
    "user_last_chat",
    "user_recent_chats",
    "user_bookmarks",
    "msg_scheduled",
    "msg_forwarded",
    "webhook_retry_queue",
    # Credential material whose owning column has no FK to ``auth_users``.
    # Deleting the account row does NOT cascade these, so they must be purged
    # explicitly or a deleted account leaves still-usable credentials behind.
    "auth_api_access_tokens",
    "auth_api_access_token_scopes",
    "auth_api_access_token_events",
    "auth_passkey_challenges",
    # Storage-quota rows key on a bare user id with no FK to ``auth_users``.
    # The override's admin ``note`` can carry PII about the person, and these
    # tables are created lazily, so a missing table is an older install.
    "user_storage_quotas",
    "storage_quota_reservations",
)


def _execute(db: Any, statement: str, params: tuple = ()) -> None:
    """Run one cleanup statement, tolerating a schema without that table."""
    try:
        db.execute(statement, params)
    except Exception as exc:
        if any(table in statement for table in _OPTIONAL_TABLES + _RESIDUE_TABLES):
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
    # The retry queue carries the full webhook payload and response history.
    # Left behind it keeps PII from the deleted server's deliveries alive.
    _execute(
        db,
        "DELETE FROM webhook_retry_queue WHERE webhook_id IN "
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
    # notif_notifications has no ON DELETE CASCADE on its message/conversation
    # foreign keys. Without this, a notification about a message here would
    # block the parent deletes on PostgreSQL and its content preview would
    # survive as orphaned residue. The per-user chat tracking rows have no
    # foreign key at all and would keep pointing at a dead conversation.
    _execute(
        db,
        "DELETE FROM notif_notifications WHERE conversation_id = ? "
        "OR message_id IN (SELECT id FROM msg_messages WHERE conversation_id = ?)",
        (cid, cid),
    )
    _execute(db, "DELETE FROM user_last_chat WHERE conversation_id = ?", (cid,))
    _execute(db, "DELETE FROM user_recent_chats WHERE conversation_id = ?", (cid,))
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
    # msg_message_tokens has an ON DELETE CASCADE on PostgreSQL, but SQLite
    # purge paths run with foreign keys disabled. Without this explicit delete
    # the per-word blind indexes of every message in the erased conversation
    # remain behind, still keyed by (now dead) message ids.
    _execute(
        db,
        "DELETE FROM msg_message_tokens WHERE message_id IN "
        "(SELECT id FROM msg_messages WHERE conversation_id = ?)",
        (cid,),
    )
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


def _is_live_server_channel_conversation(db: Any, conversation_id: int) -> bool:
    """Return True when a live server channel still owns the conversation.

    Server-channel conversations rely on server membership rather than
    ``msg_participants`` rows, so an empty participant list does not mean the
    conversation is unused. Deleting it leaves the channel pointing at a
    missing foreign-key target and every send returns 500.
    """
    try:
        row = db.fetch_one(
            "SELECT 1 AS present FROM srv_channels "
            "WHERE conversation_id = ? AND deleted = 0 LIMIT 1",
            (int(conversation_id),),
        )
    except Exception:
        return False
    return row is not None


def purge_user_conversations(db: Any, user_id: int) -> List[int]:
    """Detach a user from conversations, deleting the ones left empty.

    A DM the user shared with somebody who is still here keeps its messages
    (already anonymised by the purge) and the surviving participant's copy; a
    conversation nobody is left in -- personal notes, thread containers, a
    self-DM -- is erased with everything in it. A conversation still linked to
    a live server channel is never erased: its access is membership-backed and
    its channel would otherwise be left dangling.
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
        if (
            cid in removed
            or has_other_participants(db, cid, uid)
            or _is_live_server_channel_conversation(db, cid)
        ):
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
    "(SELECT 1 FROM thread_threads t WHERE t.conversation_id = c.id) "
    "AND NOT EXISTS "
    "(SELECT 1 FROM srv_channels ch "
    " WHERE ch.conversation_id = c.id AND ch.deleted = 0)"
)

# Thread tables are optional; without them nothing has to be excluded.
_ORPHANED_CONVERSATIONS_NO_THREADS = (
    "SELECT c.id FROM msg_conversations c WHERE NOT EXISTS "
    "(SELECT 1 FROM msg_participants p WHERE p.conversation_id = c.id) "
    "AND NOT EXISTS "
    "(SELECT 1 FROM srv_channels ch "
    " WHERE ch.conversation_id = c.id AND ch.deleted = 0)"
)

# Older databases may lack thread_threads or srv_channels; keep the original
# participant-only sweep available so cleanup never aborts on a partial schema.
_ORPHANED_CONVERSATIONS_NO_SERVER_LINKS = (
    "SELECT c.id FROM msg_conversations c WHERE NOT EXISTS "
    "(SELECT 1 FROM msg_participants p WHERE p.conversation_id = c.id) "
    "AND NOT EXISTS "
    "(SELECT 1 FROM thread_threads t WHERE t.conversation_id = c.id)"
)
_ORPHANED_CONVERSATIONS_MINIMAL = (
    "SELECT c.id FROM msg_conversations c WHERE NOT EXISTS "
    "(SELECT 1 FROM msg_participants p WHERE p.conversation_id = c.id)"
)


def purge_orphaned_conversations(db: Any) -> List[int]:
    """Delete conversations that no user participates in any more.

    A thread's conversation is created without ``msg_participants`` rows -- the
    thread's access is tracked in ``thread_members`` -- so a live thread looks
    "empty" to a participant count. Conversations a thread still points at are
    therefore left alone; a container whose thread row is gone is swept like
    any other orphan. The same applies to server channels, whose access is
    server membership: a live channel still owns its conversation even when
    no participant rows exist.
    """
    rows = None
    for query in (
        _ORPHANED_CONVERSATIONS,
        _ORPHANED_CONVERSATIONS_NO_THREADS,
        _ORPHANED_CONVERSATIONS_NO_SERVER_LINKS,
        _ORPHANED_CONVERSATIONS_MINIMAL,
    ):
        try:
            rows = _rows(db, query)
            break
        except Exception as query_error:
            # Try the next syntax variant; the last failure is what matters.
            logger.debug(f"erasure query variant failed: {query_error}")
            continue
    if rows is None:
        logger.warning("Orphan conversation sweep skipped: required tables missing")
        return []
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
    # PostgreSQL's media guard refuses to delete a ``media_files`` row while a
    # ``msg_attachments`` row still references it. The purge removes the
    # metadata first, so on PostgreSQL every attachment the account had sent
    # blocked its own erasure: the media domain raised, the metadata fallback
    # raised, and the account's uploads stayed behind. The purged user's own
    # attachment rows point at media that is going away either way, so they go
    # first.
    _execute(
        db,
        "DELETE FROM msg_attachments WHERE message_id IN "
        "(SELECT id FROM msg_messages WHERE author_id = ?)",
        (uid,),
    )
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


# === notifications, presence, profile and tracking residue ===


def purge_user_notifications(db: Any, user_id: int) -> int:
    """Delete every notification tied to the account.

    Three distinct links have to go:

    * the user's own feed (``user_id``) -- holds message content previews;
    * notifications the user *sent* (``sender_id``);
    * notifications *about the user's messages* (``message_id``) still sitting
      in somebody else's feed.

    This runs before the conversation/message purges: ``notif_notifications``
    has foreign keys to ``msg_messages``/``msg_conversations`` without
    ``ON DELETE CASCADE``, so a lingering notification both survives as PII
    residue and blocks the parent delete on PostgreSQL.
    """
    uid = int(user_id)
    before = _count(db, "SELECT count(*) AS n FROM notif_notifications")
    _execute(
        db,
        "DELETE FROM notif_notifications WHERE user_id = ? OR sender_id = ? "
        "OR message_id IN (SELECT id FROM msg_messages WHERE author_id = ?)",
        (uid, uid, uid),
    )
    for table in ("notif_settings", "notif_channel_overrides", "notif_unread"):
        _execute(db, f"DELETE FROM {table} WHERE user_id = ?", (uid,))
    return max(0, before - _count(db, "SELECT count(*) AS n FROM notif_notifications"))


def purge_user_personal_rows(db: Any, user_id: int) -> int:
    """Delete the account's presence, profile, push-token and chat-tracking rows.

    All of these tables key rows by a bare user id with no cascade from
    ``auth_users``. Left behind they keep the deleted account's status, bio,
    device tokens and recent-conversation history queryable under a dead id.
    Children go before ``pres_presence`` for the benefit of any schema that
    does declare a cascade between them.
    """
    uid = int(user_id)
    affected = 0
    for table in (
        "pres_custom_status",
        "pres_activity",
        "pres_typing",
        "pres_presence",
        "user_profiles",
        "push_tokens",
        "user_last_chat",
        "user_recent_chats",
        "user_bookmarks",
        "msg_scheduled",
        "msg_forwarded",
    ):
        column = (
            "author_id"
            if table == "msg_scheduled"
            else ("forwarded_by" if table == "msg_forwarded" else "user_id")
        )
        try:
            # Both names come from the fixed tuples above, never from input,
            # but the audit gate wants the column to pass through the identifier
            # guard rather than be trusted by convention.
            cursor = db.execute(
                f"DELETE FROM {table} WHERE {require_identifier(column)} = ?", (uid,)
            )
            affected += int(getattr(cursor, "rowcount", 0) or 0)
        except Exception as exc:
            if table not in _RESIDUE_TABLES:
                raise
            logger.debug("Erase: optional residue cleanup skipped (%s): %s", table, exc)
    return affected


def purge_user_credentials(db: Any, user_id: int) -> int:
    """Delete credentials that outlive the ``auth_users`` row.

    ``auth_api_access_tokens.created_by`` and ``auth_passkey_challenges.user_id``
    have no foreign key to ``auth_users``, so dropping the account leaves the
    user's API tokens (encrypted token material, last-used IP/UA/path) and any
    pending passkey challenges behind under a dead id. Those are live
    credentials: an attacker who reached the database could still replay a
    token or complete a challenge attributed to an account that no longer
    exists. The reaper's own purge only reaps *expired* passkey challenges and
    never touches API tokens, so this is the one place they are removed.

    Returns the number of rows deleted (children counted separately).
    """
    uid = int(user_id)
    affected = 0

    # Scope/event rows cascade from the token on PostgreSQL, but SQLite foreign
    # keys are OFF in several deployment/purge paths. Remove them explicitly
    # first, keyed on the creator's tokens, so no sensitive row is orphaned.
    for table in ("auth_api_access_token_scopes", "auth_api_access_token_events"):
        try:
            cursor = db.execute(
                f"DELETE FROM {table} WHERE token_id IN "
                "(SELECT id FROM auth_api_access_tokens WHERE created_by = ?)",
                (uid,),
            )
            affected += int(getattr(cursor, "rowcount", 0) or 0)
        except Exception as exc:
            logger.debug(
                "Erase: optional credential child cleanup skipped (%s): %s", table, exc
            )

    for table, column in (
        ("auth_api_access_tokens", "created_by"),
        ("auth_passkey_challenges", "user_id"),
    ):
        try:
            cursor = db.execute(f"DELETE FROM {table} WHERE {column} = ?", (uid,))
            affected += int(getattr(cursor, "rowcount", 0) or 0)
        except Exception as exc:
            if table not in _RESIDUE_TABLES:
                raise
            logger.debug(
                "Erase: optional credential cleanup skipped (%s): %s", table, exc
            )
    return affected


# === message content residue ===


def purge_user_message_residue(db: Any, user_id: int) -> int:
    """Scrub every residual representation of a user's message content.

    ``AccountReaper.purge_user`` (with the default ``anonymize_content``
    policy) only overwrites ``msg_messages.content``. It leaves behind the
    other copies of the same content, all keyed by the erased user id:

    * ``content_encrypted`` — the legacy ciphertext mirror of the plaintext;
    * ``content_index`` — a keyed blind index of the whole plaintext;
    * ``msg_message_tokens`` — one keyed blind index per plaintext word;
    * ``msg_edit_history.old_content`` / ``old_content_encrypted`` — previous
      (often plaintext) versions of the user's messages, keyed by editor id.

    On PostgreSQL most of these have FK cascades from ``auth_users`` or
    ``msg_messages``; the SQLite purge paths do not enforce foreign keys, so
    the rows survive after the account row is gone. Worse, a message the user
    authored in a conversation that stays alive for other participants keeps
    all of them even on PostgreSQL. Erasing only ``content`` therefore leaves
    the substance of the user's speech queryable under a dead id.

    This must run BEFORE the conversation/server purges so the token children
    are still reachable by their parent message id. Returns the number of rows
    touched (children counted separately); a missing optional table is
    tolerated so partial/older installs do not abort the erasure.
    """
    uid = int(user_id)
    affected = 0

    # Messages that survive erasure (shared DMs/groups) keep their row, so
    # blank every content-bearing column, not just ``content``.
    try:
        cursor = db.execute(
            """UPDATE msg_messages
               SET content = ?, content_encrypted = NULL, content_index = NULL,
                   metadata = NULL
             WHERE author_id = ?""",
            (ANONYMIZED_CONTENT, uid),
        )
        affected += int(getattr(cursor, "rowcount", 0) or 0)
    except Exception as exc:
        logger.debug("Erase: message content scrub skipped: %s", exc)

    try:
        cursor = db.execute(
            "DELETE FROM msg_message_tokens WHERE message_id IN "
            "(SELECT id FROM msg_messages WHERE author_id = ?)",
            (uid,),
        )
        affected += int(getattr(cursor, "rowcount", 0) or 0)
    except Exception as exc:
        logger.debug("Erase: message token blind-index scrub skipped: %s", exc)

    # Edit history is keyed by editor_id, so messages the user edited but a
    # surviving conversation still holds are covered here as well.
    for column in ("old_content", "old_content_encrypted"):
        try:
            cursor = db.execute(
                f"UPDATE msg_edit_history SET {column} = ? WHERE editor_id = ?",
                (REDACTED_HISTORY_CONTENT if column == "old_content" else None, uid),
            )
            affected += int(getattr(cursor, "rowcount", 0) or 0)
        except Exception as exc:
            logger.debug("Erase: edit-history %s scrub skipped: %s", column, exc)

    return affected


def purge_user_storage_quota(db: Any, user_id: int) -> int:
    """Delete quota overrides and reservations keyed only by a dead user id.

    ``user_storage_quotas.user_id`` and ``storage_quota_reservations.user_id``
    have no foreign key to ``auth_users``, so dropping the account leaves both
    behind under an id that no longer exists. This is a real erasure gap: the
    override row carries a free-text admin ``note`` (up to 500 chars) that
    routinely describes the person, keeping PII queryable after a GDPR purge.
    Reservations are short-lived, but a purge that lands mid-upload must not
    leave one pointing at the deleted account either.

    The tables are created lazily by the storage-quota manager, so an install
    that predates storage quotas simply has nothing to delete here - that must
    not abort the rest of the erasure.

    Returns the number of rows deleted.
    """
    uid = int(user_id)
    affected = 0
    for table in ("storage_quota_reservations", "user_storage_quotas"):
        try:
            cursor = db.execute(
                f"DELETE FROM {require_identifier(table)} "
                f"WHERE {require_identifier('user_id')} = ?",
                (uid,),
            )
            affected += int(getattr(cursor, "rowcount", 0) or 0)
        except Exception as exc:
            if table not in _RESIDUE_TABLES:
                raise
            logger.debug("Erase: optional quota cleanup skipped (%s): %s", table, exc)
    return affected


# === orchestration ===


def erase_user_residue(
    db: Any, user_id: int, media: Optional[Any] = None
) -> Dict[str, Any]:
    """Erase everything the account owned outside the ``auth_users`` row."""
    # Message content residue first, while the parent messages still exist to
    # resolve their blind-index children. A shared conversation keeps its
    # messages (and therefore their ciphertext/index residue) alive, so this
    # cannot wait for the conversation purges below.
    message_residue = purge_user_message_residue(db, user_id)
    # Notifications next: their un-cascaded FKs would otherwise block the
    # conversation/message deletes below and leak message previews.
    notifications = purge_user_notifications(db, user_id)
    personal_rows = purge_user_personal_rows(db, user_id)
    credentials = purge_user_credentials(db, user_id)
    # Quota rows have no FK cascade and the override note can hold PII, so they
    # are part of a complete erasure just like the credential rows above.
    quota_rows = purge_user_storage_quota(db, user_id)
    servers = purge_owned_servers(db, user_id)
    conversations = purge_user_conversations(db, user_id)
    unswept_media = purge_user_media(db, user_id, media=media)
    if (
        servers
        or conversations
        or unswept_media
        or notifications
        or personal_rows
        or credentials
        or quota_rows
        or message_residue
    ):
        logger.info(
            "Erase: user %s left %s message-residue row(s), "
            "%s notification(s), %s personal row(s), "
            "%s credential row(s), %s quota row(s), %s server(s), "
            "%s conversation(s), %s unswept media object(s) cleaned up",
            user_id,
            message_residue,
            notifications,
            personal_rows,
            credentials,
            quota_rows,
            len(servers),
            len(conversations),
            len(unswept_media),
        )
    return {
        "message_residue": message_residue,
        "notifications": notifications,
        "personal_rows": personal_rows,
        "credentials": credentials,
        "storage_quota": quota_rows,
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

    # Accounts purged by an older build kept their API tokens and passkey
    # challenges (no FK cascade). Find the dead owners and reap the residue so
    # a pre-fix deployment cannot keep live credentials for deleted accounts.
    orphan_credential_owners = set()
    for query, column in (
        (
            "SELECT DISTINCT a.created_by AS owner FROM auth_api_access_tokens a "
            "LEFT JOIN auth_users u ON u.id = a.created_by WHERE u.id IS NULL",
            "owner",
        ),
        (
            "SELECT DISTINCT p.user_id AS owner FROM auth_passkey_challenges p "
            "LEFT JOIN auth_users u ON u.id = p.user_id WHERE u.id IS NULL",
            "owner",
        ),
    ):
        try:
            for row in _rows(db, query):
                if row[column] is not None:
                    orphan_credential_owners.add(int(row[column]))
        except Exception as exc:
            logger.debug("Erase: orphan credential sweep query skipped: %s", exc)
    for owner in orphan_credential_owners:
        purge_user_credentials(db, owner)

    # Accounts purged by a build that predates the quota cleanup kept their
    # override (and its PII-bearing admin note) and any in-flight reservations
    # under a dead id. Reap those the same way as the credential residue.
    orphan_quota_owners = set()
    for table in ("user_storage_quotas", "storage_quota_reservations"):
        try:
            for row in _rows(
                db,
                f"SELECT DISTINCT q.user_id AS owner FROM {require_identifier(table)} q "
                "LEFT JOIN auth_users u ON u.id = q.user_id WHERE u.id IS NULL",
            ):
                if row["owner"] is not None:
                    orphan_quota_owners.add(int(row["owner"]))
        except Exception as exc:
            logger.debug("Erase: orphan quota sweep query skipped: %s", exc)
    for owner in orphan_quota_owners:
        purge_user_storage_quota(db, owner)

    result = {
        "servers": sweep_orphaned_servers(db),
        "conversations": purge_orphaned_conversations(db),
        "media_unswept": unswept,
        "credentials": sorted(orphan_credential_owners),
        "storage_quota": sorted(orphan_quota_owners),
    }
    if (
        result["servers"]
        or result["conversations"]
        or unswept
        or orphan_credential_owners
        or orphan_quota_owners
    ):
        logger.info(
            "Erase: swept %s orphan server(s), %s orphan conversation(s), "
            "%s unswept media object(s), %s orphan credential owner(s), "
            "%s orphan quota owner(s)",
            len(result["servers"]),
            len(result["conversations"]),
            len(unswept),
            len(orphan_credential_owners),
            len(orphan_quota_owners),
        )
    return result


__all__ = [
    "ANONYMIZED_CONTENT",
    "REDACTED_HISTORY_CONTENT",
    "delete_conversation",
    "erase_user_residue",
    "has_other_participants",
    "purge_orphaned_conversations",
    "purge_owned_servers",
    "purge_server_recursive",
    "purge_user_conversations",
    "purge_user_message_residue",
    "purge_user_credentials",
    "purge_user_media",
    "purge_user_notifications",
    "purge_user_personal_rows",
    "purge_user_storage_quota",
    "sweep_orphaned_servers",
    "sweep_orphans",
]
