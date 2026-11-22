import re
from typing import Optional, List, Dict, Any

import utils.logger as logger

from ..models import (
    Notification,
    NotificationSettings,
    ChannelNotificationOverride,
    MentionType,
    NotificationLevel,
)
from src.core.base import SnowflakeID


# Notification previews are copied verbatim into websocket events, push
# payloads, log lines and the HTTP response. C0/C1 controls and DEL let a
# message author forge a log line or escape a terminal; bidi overrides and
# zero-width format characters let them visually disguise a link or reverse
# the apparent direction of a preview. None of these are legitimate in a
# single-line preview, so strip them at every read/write boundary.
# This set must stay in lockstep with ``hardening.text.CONTROL_CHARACTERS``
# (the rule every header/log/URL consumer uses). It used to stop at U+2069,
# leaving the word joiner (U+2060), the legacy directional formats (U+206A-
# U+206F) and the Syriac abbreviation mark (U+070F) intact: those are invisible
# Cf characters a message author can smuggle into a preview to glue words
# together or change how a link renders on a device. Reject the full set here.
_UNSAFE_PREVIEW_CHARS = re.compile(
    "[\x00-\x1f\x7f-\x9f"
    "\u061c\u070f\u00ad"  # Arabic letter mark, Syriac abbrev mark, soft hyphen
    "\u200b-\u200f"  # zero-width space/joiners + LRM/RLM
    "\u2028-\u202e"  # line/para separators + bidi embedding/overrides
    "\u2060\u2066-\u206f"  # word joiner + bidi isolates + legacy formats
    "\ufeff]"  # BOM
)


def sanitize_notification_content(content: Optional[str]) -> str:
    """Return a single-line preview free of control/format characters.

    Removed characters are replaced with a space (rather than deleted) so
    adjacent words do not merge, then whitespace runs are collapsed.
    """
    if not content:
        return ""
    cleaned = _UNSAFE_PREVIEW_CHARS.sub(" ", str(content))
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def row_to_notification(row: Dict[str, Any]) -> Notification:
    content_preview = sanitize_notification_content(row.get("content_preview"))
    if row.get("content_preview_encrypted"):
        try:
            from src.utils.encryption import decrypt_data

            content_preview = sanitize_notification_content(
                decrypt_data(
                    row["content_preview_encrypted"],
                    context=f"notification:{row['id']}",
                )
            )
        except Exception as e:
            logger.warning(
                f"Failed to decrypt notification content_preview for {row['id']}: {e}"
            )
    return Notification(
        id=row["id"],
        user_id=row["user_id"],
        author_id=row["sender_id"],
        message_id=row["message_id"],
        conversation_id=row["conversation_id"],
        server_id=row["server_id"],
        channel_id=row["channel_id"],
        thread_id=row.get("thread_id"),
        mention_type=MentionType(row["mention_type"]),
        content_preview=content_preview,
        read=bool(row["read"]),
        created_at=row["created_at"],
    )


def row_to_settings(row: Dict[str, Any]) -> NotificationSettings:
    return NotificationSettings(
        user_id=row["user_id"],
        server_id=row["server_id"],
        level=NotificationLevel(row["level"]),
        dm_notifications=bool(row["dm_notifications"]),
        suppress_everyone=bool(row["suppress_everyone"]),
        suppress_roles=bool(row["suppress_roles"]),
        mobile_push=bool(row["mobile_push"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_channel_override(row: Dict[str, Any]) -> ChannelNotificationOverride:
    """Convert database row to ChannelNotificationOverride.

    Consolidates the two identical original methods _row_to_override
    and _row_to_channel_override into one.
    """
    return ChannelNotificationOverride(
        user_id=row["user_id"],
        channel_id=row["channel_id"],
        level=NotificationLevel(row["level"]),
        muted_until=row["muted_until"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_message(db, message_id: SnowflakeID) -> Optional[Dict]:
    return db.fetch_one(
        "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
    )


def get_conversation(db, conversation_id: SnowflakeID) -> Optional[Dict]:
    return db.fetch_one(
        "SELECT * FROM msg_conversations WHERE id = ? AND deleted = 0",
        (conversation_id,),
    )


def get_conversation_participants(db, conversation_id: int) -> List[int]:
    rows = db.fetch_all(
        "SELECT user_id FROM msg_participants WHERE conversation_id = ?",
        (conversation_id,),
    )
    return [row["user_id"] for row in rows]


def is_blocked_by_either(relationships, user_id: int, other_id: int) -> bool:
    if not relationships:
        return False
    return relationships.is_blocked_by_either(user_id, other_id)


def get_blocked_user_ids(relationships, db, user_id: int) -> set:
    blocked = set()
    if relationships:
        blocked.update(relationships.get_all_blocked_ids(user_id))
        rows = db.fetch_all(
            "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?", (user_id,)
        )
        for row in rows:
            blocked.add(row["blocker_id"])
    return blocked


def truncate_content(content: str, config: Dict[str, Any]) -> str:
    max_len = max(3, config.get("content_preview_length", 100))
    # Sanitize before truncating so a control character can never survive in
    # the stored preview (including when a cut splits a format sequence).
    safe = sanitize_notification_content(content)
    if len(safe) <= max_len:
        return safe
    return safe[: max_len - 3] + "..."


def role_exists(db, role_id: SnowflakeID) -> bool:
    row = db.fetch_one("SELECT 1 FROM srv_roles WHERE id = ?", (role_id,))
    return row is not None


def get_role(db, role_id: SnowflakeID) -> Optional[Dict]:
    return db.fetch_one("SELECT * FROM srv_roles WHERE id = ?", (role_id,))


def role_covers_all_members(
    db,
    role_id: SnowflakeID,
    server_id: SnowflakeID,
    exclude_user_id: Optional[SnowflakeID] = None,
) -> bool:
    """Whether the role reaches the entire audience an @everyone would reach.

    A non-default role assigned to the whole server is an @everyone-equivalent
    mass ping: ``NotificationCreatorMixin.create_notifications`` fans a ROLE
    mention out to all of that role's members, exactly like the default
    ``is_default = 1`` role.  Checking the ``is_default`` flag alone lets such
    a "recreated everyone" role evade the mass-mention gate.

    The fan-out always skips the message author, so a role held by every member
    *except the author* reaches precisely the same audience as @everyone.
    ``exclude_user_id`` is therefore removed from BOTH counts: the verdict is
    true when the role covers every member the fan-out could actually notify.
    Omitting it measures literal full-server coverage (useful for callers that
    do not have an author).

    Bounded set-based counts keep this to a fixed number of round trips (no
    per-member scans), independent of member/role counts.  A well-formed
    ``COUNT(*)`` always returns a row with the ``n`` alias; a query failure
    fails closed (treat as covering everyone, so an unverifiable audience
    cannot authorize a mass ping).  A backend that cannot answer these counts
    at all (e.g. a minimal stub) keeps the pre-existing role-mention behaviour
    rather than denying legitimate pings.  A server with no other members is
    not classified as a mass ping (nothing to fan out to).
    """
    try:
        if exclude_user_id is None:
            total = db.fetch_one(
                "SELECT COUNT(*) AS n FROM srv_members WHERE server_id = ?",
                (server_id,),
            )
            covered = db.fetch_one(
                """SELECT COUNT(DISTINCT m.user_id) AS n
                   FROM srv_member_roles mr
                   JOIN srv_members m ON mr.member_id = m.id
                   WHERE mr.role_id = ? AND m.server_id = ?""",
                (role_id, server_id),
            )
        else:
            total = db.fetch_one(
                "SELECT COUNT(*) AS n FROM srv_members "
                "WHERE server_id = ? AND user_id != ?",
                (server_id, exclude_user_id),
            )
            covered = db.fetch_one(
                """SELECT COUNT(DISTINCT m.user_id) AS n
                   FROM srv_member_roles mr
                   JOIN srv_members m ON mr.member_id = m.id
                   WHERE mr.role_id = ? AND m.server_id = ? AND m.user_id != ?""",
                (role_id, server_id, exclude_user_id),
            )
    except Exception:
        return True
    if not total or not covered or "n" not in total or "n" not in covered:
        return False
    try:
        return int(total["n"]) > 0 and int(covered["n"]) >= int(total["n"])
    except (TypeError, ValueError):
        return True


def channel_exists(db, channel_id: SnowflakeID) -> bool:
    row = db.fetch_one("SELECT 1 FROM srv_channels WHERE id = ?", (channel_id,))
    return row is not None


def get_channel(db, channel_id: SnowflakeID) -> Optional[Dict]:
    return db.fetch_one("SELECT * FROM srv_channels WHERE id = ?", (channel_id,))


def get_server_members(db, server_id: SnowflakeID) -> List[SnowflakeID]:
    rows = db.fetch_all(
        "SELECT user_id FROM srv_members WHERE server_id = ?", (server_id,)
    )
    return [row["user_id"] for row in rows]


def get_role_members(db, role_id: SnowflakeID) -> List[SnowflakeID]:
    rows = db.fetch_all(
        """SELECT m.user_id FROM srv_member_roles mr
           JOIN srv_members m ON mr.member_id = m.id
           WHERE mr.role_id = ?""",
        (role_id,),
    )
    return [row["user_id"] for row in rows]


def has_mention_everyone_permission(
    servers,
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    channel_id: Optional[SnowflakeID] = None,
) -> bool:
    """Whether ``user_id`` may use @everyone/@here in this server/channel.

    Fail closed: an unwired servers module means the permission cannot be
    evaluated, so it is DENIED. This previously returned ``True``, which
    contradicted the documented policy in ``MentionValidationMixin`` ("denied
    by default so a deployment that hasn't fully wired the servers module
    cannot accidentally spam everyone"). The one current caller already
    denies before reaching here; fixing the helper keeps it independently
    safe for future callers.
    """
    if not servers:
        return False
    return servers.has_permission(
        user_id, server_id, "messages.mention_everyone", channel_id
    )


def get_online_members(presence, db, server_id: SnowflakeID) -> List[SnowflakeID]:
    if not presence:
        return []
    return presence.get_online_server_members(0, server_id)
