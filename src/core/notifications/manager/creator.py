from typing import Optional, List, Dict

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.events.types import EventType
from src.utils.encryption import encrypt_data
from ..models import (
    MentionType,
    Notification,
    NotificationLevel,
    NotificationSettings,
    ChannelNotificationOverride,
)
from .helpers import (
    get_blocked_user_ids,
    get_conversation_participants,
    get_role_members,
    get_server_members,
    get_online_members,
    sanitize_notification_content,
    truncate_content,
)


from .protocol import NotificationProtocol


# Bound the ``user_id IN (...)`` fan-out of a bulk unread bump. A role/everyone
# mention is not capped by this module: one message can hand thousands of ids
# to the fan-out. Keeping each statement to this many binds avoids the backend
# variable ceiling and keeps the notification write transaction short-lived.
_UNREAD_BULK_CHUNK_SIZE = 500

# Bound the ``role_id IN (...)`` hydration below so a message packing many
# role mentions still issues statements under the backend bind-variable cap.
_ROLE_MEMBER_CHUNK = 500


def get_role_members_bulk(db, role_ids) -> Dict[SnowflakeID, List[SnowflakeID]]:
    """Resolve ``role_id -> member user ids`` with bounded ``IN`` queries.

    This is the set-based twin of :func:`get_role_members`: it uses the exact
    same tables and join (``srv_member_roles`` joined to ``srv_members`` on
    ``member_id``) and the same lack of extra predicates, so a member is
    returned for a role here if and only if ``get_role_members(db, role)``
    would have returned them. The only difference is that all distinct role
    ids are resolved together instead of one SELECT per role (an N+1 on any
    message that mentions several roles).

    Security: the ``role_id IN (...)`` set is built exclusively from the ids
    the caller already decided to hydrate, so a member of an *unmentioned*
    role can never enter the result. Ids that are not integers are skipped —
    such a value could not match an integer role id in the per-row query
    either. No viewer/organisation column exists on this join, so batching
    widens nothing: the predicate is role-id equality in both forms.
    """
    unique: List[int] = []
    seen = set()
    for rid in role_ids:
        try:
            key = int(rid)
        except (TypeError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            unique.append(key)

    by_role: Dict[SnowflakeID, List[SnowflakeID]] = {}
    for start in range(0, len(unique), _ROLE_MEMBER_CHUNK):
        chunk = unique[start : start + _ROLE_MEMBER_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.fetch_all(
            f"""SELECT mr.role_id, m.user_id FROM srv_member_roles mr
                JOIN srv_members m ON mr.member_id = m.id
                WHERE mr.role_id IN ({placeholders})""",
            tuple(chunk),
        )
        for row in rows or []:
            try:
                role_id = int(row["role_id"])
            except (KeyError, TypeError, ValueError):
                continue
            by_role.setdefault(role_id, []).append(row["user_id"])
    return by_role


def _encrypt_content_preview(preview: Optional[str], notif_id: int) -> Optional[str]:
    if not preview:
        return None
    try:
        return encrypt_data(preview, context=f"notification:{notif_id}")
    except Exception as e:
        logger.warning(
            f"Failed to encrypt content_preview for notification {notif_id}: {e}"
        )
        return preview


class NotificationCreatorMixin(NotificationProtocol):
    def create_notifications_for_message(
        self,
        message_id: SnowflakeID,
        author_id: Optional[SnowflakeID] = None,
        conversation_id: Optional[SnowflakeID] = None,
        content: str = "",
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
        thread_id: Optional[SnowflakeID] = None,
        **kwargs,
    ) -> List[Notification]:
        actual_author_id = author_id or kwargs.get("sender_id")
        if actual_author_id is None:
            raise ValueError("author_id or sender_id must be provided")
        if conversation_id is None:
            raise ValueError("conversation_id must be provided")

        return self.create_notifications(
            message_id=message_id,
            author_id=actual_author_id,
            conversation_id=conversation_id,
            content=content,
            server_id=server_id,
            channel_id=channel_id,
            thread_id=thread_id,
        )

    def create_notifications(
        self,
        message_id: SnowflakeID,
        author_id: SnowflakeID,
        conversation_id: SnowflakeID,
        content: str,
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
        thread_id: Optional[SnowflakeID] = None,
    ) -> List[Notification]:
        if not conversation_id:
            raise ValueError("conversation_id must be provided")
        mentions = self.parse_mentions(content)
        validated = self.validate_mentions(author_id, mentions, server_id, channel_id)

        users_to_notify = set()
        mention_types = {}

        blocked_users = get_blocked_user_ids(self._relationships, self._db, author_id)

        # ROLE-member hydration N+1: a message mentioning several roles used to
        # issue one ``SELECT ... srv_member_roles JOIN srv_members WHERE
        # role_id = ?`` per role. Resolve every distinct valid role id with a
        # single bounded ``IN`` query + in-memory map first, then reuse it in
        # the fan-out below. The map preserves each role's member list, so the
        # per-recipient decisions (author/blocked/type de-dup) are unchanged.
        role_ids = [
            m.target_id
            for m in validated
            if m.valid
            and m.mention_type == MentionType.ROLE
            and m.target_id is not None
        ]
        role_members_by_id = None  # None => per-role fallback (exact old path)
        if role_ids:
            try:
                role_members_by_id = get_role_members_bulk(self._db, role_ids)
            except Exception as e:
                # A legacy/stub DB without the set-based query keeps the
                # original per-role behaviour rather than losing the ping.
                logger.debug(f"bulk role-member hydration failed: {e}")
                role_members_by_id = None

        for mention in validated:
            if not mention.valid:
                continue

            if mention.mention_type == MentionType.USER:
                target_id = mention.target_id
                if target_id != author_id and target_id not in blocked_users:
                    users_to_notify.add(target_id)
                    mention_types[target_id] = MentionType.USER

            elif mention.mention_type == MentionType.ROLE:
                if mention.target_id is None:
                    continue
                if role_members_by_id is None:
                    role_members = get_role_members(self._db, mention.target_id)
                else:
                    try:
                        role_members = role_members_by_id.get(
                            int(mention.target_id), []
                        )
                    except (TypeError, ValueError):
                        role_members = []
                for member_id in role_members:
                    if member_id != author_id and member_id not in blocked_users:
                        if member_id not in mention_types:
                            users_to_notify.add(member_id)
                            mention_types[member_id] = MentionType.ROLE

            elif mention.mention_type == MentionType.CHANNEL:
                # Channel mentions notify members who have access to the channel
                if mention.target_id is None:
                    continue
                if channel_id and mention.target_id == channel_id:
                    # Get members who can view this channel
                    if server_id:
                        server_members = get_server_members(self._db, server_id)
                        for member_id in server_members:
                            if (
                                member_id != author_id
                                and member_id not in blocked_users
                            ):
                                if member_id not in mention_types:
                                    users_to_notify.add(member_id)
                                    mention_types[member_id] = MentionType.CHANNEL

            elif mention.mention_type == MentionType.EVERYONE:
                if server_id:
                    server_members = get_server_members(self._db, server_id)
                    for member_id in server_members:
                        if member_id != author_id and member_id not in blocked_users:
                            if member_id not in mention_types:
                                users_to_notify.add(member_id)
                                mention_types[member_id] = MentionType.EVERYONE

            elif mention.mention_type == MentionType.HERE:
                if server_id:
                    online_members = get_online_members(
                        self._presence, self._db, server_id
                    )
                    for member_id in online_members:
                        if member_id != author_id and member_id not in blocked_users:
                            if member_id not in mention_types:
                                users_to_notify.add(member_id)
                                mention_types[member_id] = MentionType.HERE

        # SECURITY: never notify (or leak content_preview to) users who
        # cannot view the channel the message was posted in. This filters
        # every fan-out type (user/role/channel/@everyone/@here).
        users_to_notify = self._filter_recipients_by_channel_view(
            users_to_notify, server_id, channel_id
        )

        # SECURITY: a DM has no server/channel context, so the channel-view
        # filter above is a no-op there. A mention alone is NOT authority to
        # read a DM: only actual participants of the conversation may receive
        # a notification carrying its private content preview (and, on push
        # dispatch, a push payload). Without this gate a caller could mention
        # an unrelated user in a DM and leak the private body to them.
        if server_id is None:
            users_to_notify = self._filter_recipients_by_conversation(
                users_to_notify, conversation_id
            )

        now = self._get_timestamp()
        preview = truncate_content(content, self._config)

        user_ids_list = list(users_to_notify)
        all_settings = self.get_notification_settings_bulk(user_ids_list, server_id)
        all_overrides = {}
        if channel_id:
            all_overrides = self.get_channel_overrides_bulk(user_ids_list, channel_id)

        # The only presence field this fan-out consumes is the recipient's
        # focused-channel id (to suppress a ping when they are already looking
        # at the channel). Hydrate the whole audience with ONE bulk read instead
        # of one ``get_presence`` call per recipient: ``get_presences`` resolves
        # cache hits with an MGET and cache misses with chunked ``IN (...)``
        # queries, so a role/everyone mention no longer pays 2-3 round trips
        # per user. For a DM (no channel) focus is never consulted, so skip the
        # lookup entirely.
        presences_by_user: dict = {}
        if self._presence and channel_id and user_ids_list:
            try:
                presences_by_user = {
                    int(getattr(p, "user_id", uid)): p
                    for uid, p in zip(
                        user_ids_list,
                        self._presence.get_presences(user_ids_list),
                    )
                    if p is not None
                }
            except Exception as e:
                # Fail open (deliver the ping): a broken presence store must
                # neither crash the send path nor suppress notifications based
                # on stale/garbage focus data. Never fall back to per-user
                # reads here — that would restore the N+1 on the failure path.
                logger.debug(f"Bulk focus hydration failed: {e}")

        final_users_to_notify = []
        for user_id in user_ids_list:
            mention_type = mention_types.get(user_id)
            if not mention_type:
                continue

            settings = all_settings.get(user_id)
            override = all_overrides.get(user_id)

            if self._should_notify_user_optimized(
                user_id,
                author_id,
                server_id,
                channel_id,
                mention_type,
                settings,
                override,
                presences_by_user=presences_by_user,
            ):
                final_users_to_notify.append(user_id)

        if final_users_to_notify:
            return self.create_notifications_bulk(
                user_ids=final_users_to_notify,
                author_id=author_id,
                message_id=message_id,
                conversation_id=conversation_id,
                server_id=server_id,
                channel_id=channel_id,
                thread_id=thread_id,
                mention_types=mention_types,
                content_preview=preview,
                created_at=now,
            )

        return []

    def _existing_notification_users(self, message_id: SnowflakeID) -> set:
        """Users who already have a notification row for ``message_id``.

        The table carries UNIQUE(user_id, message_id); checking upfront keeps
        repeat fan-outs (send-path hook plus explicit caller) idempotent
        instead of dying on an IntegrityError.
        """
        try:
            rows = self._db.fetch_all(
                "SELECT user_id FROM notif_notifications WHERE message_id = ?",
                (message_id,),
            )
            return {row["user_id"] for row in rows or []}
        except Exception as e:
            logger.debug(f"Existing-notification lookup failed: {e}")
            return set()

    def _filter_recipients_by_channel_view(
        self,
        user_ids: set,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
    ) -> set:
        """Keep only recipients who may view ``channel_id``.

        Uses the servers module's cached permission lookup (per-user result
        is memoised there), so fan-out cost stays bounded. DM contexts
        (``channel_id`` unset) skip the check because participants are
        already scoped by conversation membership; any per-user check error
        excludes that user (fail-closed) so a broken checker can never leak
        content previews. A channel-scoped fan-out with NO permission system
        available also fails closed: an unverifiable channel audience must
        never receive previews.
        """
        recipients = set(user_ids)
        if not recipients:
            return recipients

        if not channel_id:
            # DM / group-DM context — no channel to gate on.
            return recipients

        servers = getattr(self, "_servers", None)
        has_permission = getattr(servers, "has_permission", None)
        if not callable(has_permission):
            logger.warning(
                "Notification fan-out skipped: channel-scoped recipients "
                f"cannot be permission-checked (message channel {channel_id})"
            )
            return set()

        allowed = set()
        for user_id in recipients:
            try:
                if has_permission(user_id, server_id, "channels.view", channel_id):
                    allowed.add(user_id)
            except Exception as e:
                logger.debug(
                    f"Channel view check failed for user {user_id}; "
                    f"excluding from notification fan-out: {e}"
                )
        return allowed

    def _filter_recipients_by_conversation(
        self, user_ids: set, conversation_id: SnowflakeID
    ) -> set:
        """Keep only recipients who participate in ``conversation_id``.

        Used for DM / group-DM contexts (``server_id is None``), where there is
        no channel to authorize against and the channel-view gate is a no-op.
        Conversation membership is the only authority for receiving a DM's
        private content preview. The lookup is fail-closed: an error, an
        unavailable participant store, or an empty participant set excludes
        every recipient, so an unverifiable DM audience can never be notified.
        """
        recipients = set(user_ids)
        if not recipients:
            return recipients

        def _as_int(value):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        try:
            participants = get_conversation_participants(self._db, conversation_id)
        except Exception as e:
            logger.warning(
                "DM mention fan-out skipped: conversation participants could "
                f"not be resolved (conversation {conversation_id}): {e}"
            )
            return set()

        allowed = {
            pid
            for pid in (_as_int(uid) for uid in (participants or []))
            if pid is not None
        }
        if not allowed:
            logger.warning(
                "DM mention fan-out skipped: no participants resolved for "
                f"conversation {conversation_id}"
            )
            return set()
        return {uid for uid in recipients if _as_int(uid) in allowed}

    def _should_notify_user_optimized(
        self,
        user_id: SnowflakeID,
        author_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        mention_type: MentionType,
        settings: Optional["NotificationSettings"],
        override: Optional["ChannelNotificationOverride"],
        presences_by_user: Optional[dict] = None,
    ) -> bool:
        # Check if user is currently focused on this channel. ``presences_by_user``
        # is the fan-out's single bulk hydration; direct callers (e.g. automod
        # alerts) leave it unset and keep the legacy single-user lookup.
        if self._presence and channel_id:
            try:
                if presences_by_user is not None:
                    presence = presences_by_user.get(int(user_id))
                else:
                    presence = self._presence.get_presence(user_id)
                if presence is not None:
                    focused_id = getattr(presence, "current_channel_id", None)
                    if focused_id and int(focused_id) == int(channel_id):
                        logger.debug(
                            f"Suppressing notification for user {user_id} - already focused on channel {channel_id}"
                        )
                        return False
            except Exception as e:
                logger.debug(
                    f"Failed to check user focus during notification logic: {e}"
                )

        if override:
            now = self._get_timestamp()
            is_mute_active = override.muted_until is None or override.muted_until > now
            if override.level == NotificationLevel.MUTED and is_mute_active:
                return False
            if override.level == NotificationLevel.NOTHING:
                return False

        # M2 (muted fallback): a MUTED level on the resolved settings row
        # is an indefinite mute and must suppress. For DMs (server_id None)
        # the resolved row IS the global one and no per-channel override is
        # on the path (channel_id unset), so without this check a
        # globally-muted user still receives DM notifications. The explicit
        # per-channel override above stays authoritative: an active
        # per-channel mute (or NOTHING) already returned False, while an
        # expired per-channel mute falls through to here and inherits the
        # global/per-server mute instead of unmuting.
        if (
            not settings
            or settings.level == NotificationLevel.NOTHING
            or settings.level == NotificationLevel.MUTED
        ):
            return False

        # M2: the per-user ``dm_notifications`` preference was stored and
        # served but never consulted, so a user who disabled DM
        # notifications still got mentioned in direct messages. There is no
        # server scope on a DM, so server_id is None exactly there; a global
        # level of NOTHING/MUTED above already silenced those users.
        if server_id is None and not settings.dm_notifications:
            return False

        if mention_type in (MentionType.EVERYONE, MentionType.HERE):
            if settings.suppress_everyone:
                return False

        if mention_type == MentionType.ROLE:
            if settings.suppress_roles:
                return False

        return True

    def _create_notification(
        self,
        user_id: SnowflakeID,
        author_id: SnowflakeID,
        message_id: SnowflakeID,
        conversation_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        thread_id: Optional[SnowflakeID],
        mention_type: MentionType,
        content_preview: str,
        created_at: int,
    ) -> Optional[Notification]:
        notif_id = self._generate_id()
        # Direct callers (e.g. automod alerts) bypass ``truncate_content``, so
        # sanitize here too before the preview is encrypted/stored/dispatched.
        safe_preview = sanitize_notification_content(content_preview)
        content_preview_encrypted = _encrypt_content_preview(safe_preview, notif_id)

        if content_preview_encrypted and content_preview_encrypted != safe_preview:
            content_preview_to_store = ""
        else:
            content_preview_to_store = safe_preview
            content_preview_encrypted = None

        # Idempotent on UNIQUE(user_id, message_id): a duplicate insert is a
        # no-op rather than an IntegrityError.
        self._db.execute(
            """INSERT OR IGNORE INTO notif_notifications
               (id, user_id, sender_id, message_id, conversation_id, server_id, channel_id, thread_id,
                mention_type, content_preview, content_preview_encrypted, read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                notif_id,
                user_id,
                author_id,
                message_id,
                conversation_id,
                server_id,
                channel_id,
                thread_id,
                mention_type.value,
                content_preview_to_store,
                content_preview_encrypted,
                created_at,
            ),
        )

        # Scope the read-back to the owner. An unscoped lookup here is an IDOR
        # primitive: if the generated id collides with an existing row (e.g.
        # after a DB import/restore), the unscoped read would hand back that
        # other user's notification and its decrypted preview, which is then
        # dispatched to ``user_id`` over the websocket and push channels.
        notification = self.get_notification(notif_id, user_id)
        if notification:
            self._dispatch_notification_event(
                user_id,
                EventType.NOTIFICATION_CREATE,
                {
                    "id": str(notification.id),
                    "type": "mention",
                    "title": self._build_notification_title(mention_type, author_id),
                    "content": notification.content_preview or "",
                    "content_preview": notification.content_preview or "",
                    "read": bool(notification.read),
                    "created_at": notification.created_at,
                    "link": self._build_notification_link(
                        server_id, channel_id, message_id
                    ),
                    "sender_id": str(notification.author_id),
                },
            )
            self._queue_push_delivery([notification])

        return notification

    def _build_notification_title(
        self, mention_type: MentionType, author_id: SnowflakeID
    ) -> str:
        if mention_type == MentionType.USER:
            return "You were mentioned"
        elif mention_type == MentionType.ROLE:
            return "Your role was mentioned"
        elif mention_type in (MentionType.EVERYONE, MentionType.HERE):
            return f"@{mention_type.value} mention"
        return "New Notification"

    def _build_notification_link(
        self,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        message_id: SnowflakeID,
    ) -> Optional[str]:
        if server_id and channel_id:
            return f"/channels/{server_id}/{channel_id}/{message_id}"
        if server_id:
            return f"/channels/{server_id}/{message_id}"
        return None

    def _bump_unread_counts_bulk(
        self,
        user_ids: List[SnowflakeID],
        conversation_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        is_mention: bool = True,
    ) -> None:
        """Bump unread counters for a whole fan-out in bounded SQL batches.

        The old bulk path called ``_update_unread_count`` once per recipient.
        That helper issues a SELECT followed by an UPDATE or INSERT, so a
        role/everyone mention in a large server held the notification write
        transaction open for ``2 * N`` round trips. Both counters live on one
        ``notif_unread`` row per (user, conversation), so one scoped read plus
        two ``execute_many`` batches per bounded chunk is enough.

        Increments are applied in SQL (``col = col + 1``) rather than computed
        in Python from the read values. Two workers can therefore bump the same
        row concurrently without either losing the other's increment -- a
        read-modify-write would overwrite it. Every statement carries both the
        caller's ``conversation_id`` and an explicit user-id chunk, so a
        recipient can only ever have its own counters changed. A missing row is
        inserted with ``INSERT OR IGNORE``: if another worker inserted it in
        between, that worker's increment stands (a rare undercount is safe;
        a cross-user write is not).
        """
        ids = list(dict.fromkeys(int(uid) for uid in user_ids))
        if not ids:
            return
        now = self._get_timestamp()
        mention_delta = 1 if is_mention else 0

        for start in range(0, len(ids), _UNREAD_BULK_CHUNK_SIZE):
            chunk = ids[start : start + _UNREAD_BULK_CHUNK_SIZE]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._db.fetch_all(
                "SELECT id, user_id FROM notif_unread "
                f"WHERE conversation_id = ? AND user_id IN ({placeholders})",
                (conversation_id, *chunk),
            )
            existing_ids = {int(row["user_id"]): row["id"] for row in rows or []}

            updates = [
                (1, mention_delta, now, existing_ids[uid], conversation_id)
                for uid in chunk
                if uid in existing_ids
            ]
            inserts = [
                (
                    self._generate_id(),
                    uid,
                    conversation_id,
                    server_id,
                    channel_id,
                    mention_delta,
                    now,
                )
                for uid in chunk
                if uid not in existing_ids
            ]

            if updates:
                self._db.execute_many(
                    "UPDATE notif_unread SET unread_count = unread_count + ?, "
                    "mention_count = mention_count + ?, updated_at = ? "
                    "WHERE id = ? AND conversation_id = ?",
                    updates,
                    auto_commit=False,
                )
            if inserts:
                self._db.execute_many(
                    "INSERT OR IGNORE INTO notif_unread "
                    "(id, user_id, conversation_id, server_id, channel_id, "
                    "unread_count, mention_count, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    inserts,
                    auto_commit=False,
                )

    def create_notifications_bulk(
        self,
        user_ids: List[SnowflakeID],
        author_id: SnowflakeID,
        message_id: SnowflakeID,
        conversation_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        thread_id: Optional[SnowflakeID],
        mention_types: Dict[int, MentionType],
        content_preview: str,
        created_at: int,
    ) -> List[Notification]:
        # Defense-in-depth: UNIQUE(user_id, message_id) makes repeat fan-outs
        # for the same message no-ops instead of IntegrityErrors.
        already_notified = self._existing_notification_users(message_id)
        user_ids = [uid for uid in user_ids if uid not in already_notified]
        if not user_ids:
            return []

        notifications_to_insert = []
        notif_ids: List[SnowflakeID] = []
        for uid in user_ids:
            notif_id = self._generate_id()
            notif_ids.append(notif_id)
            mention_type = mention_types.get(uid, MentionType.USER)
            content_preview_encrypted = _encrypt_content_preview(
                content_preview, notif_id
            )
            if (
                content_preview_encrypted
                and content_preview_encrypted != content_preview
            ):
                content_preview_to_store = ""
            else:
                content_preview_to_store = content_preview
                content_preview_encrypted = None
            notifications_to_insert.append(
                (
                    notif_id,
                    uid,
                    author_id,
                    message_id,
                    conversation_id,
                    server_id,
                    channel_id,
                    thread_id,
                    mention_type.value,
                    content_preview_to_store,
                    content_preview_encrypted,
                    0,
                    created_at,
                )
            )

        self._db.begin_transaction()
        try:
            self._db.execute_many(
                """INSERT OR IGNORE INTO notif_notifications
                   (id, user_id, sender_id, message_id, conversation_id, server_id, channel_id, thread_id,
                    mention_type, content_preview, content_preview_encrypted, read, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                notifications_to_insert,
            )

            self._bump_unread_counts_bulk(
                user_ids, conversation_id, server_id, channel_id, is_mention=True
            )

            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        results = []

        for data in notifications_to_insert:
            notif_preview = data[9]
            if data[10]:
                try:
                    from src.utils.encryption import decrypt_data

                    notif_preview = decrypt_data(
                        data[10], context=f"notification:{data[0]}"
                    )
                except Exception as decrypt_error:
                    # Fall back to the stored plaintext body below.
                    logger.debug(
                        f"notification body decryption failed: {decrypt_error}"
                    )

            notif = Notification(
                id=data[0],
                user_id=data[1],
                author_id=data[2],
                message_id=data[3],
                conversation_id=data[4],
                server_id=data[5],
                channel_id=data[6],
                thread_id=data[7],
                mention_type=MentionType(data[8]),
                content_preview=notif_preview,
                read=False,
                created_at=data[12],
            )
            results.append(notif)

            self._dispatch_notification_event(
                notif.user_id,
                EventType.NOTIFICATION_CREATE,
                {
                    "id": str(notif.id),
                    "type": "mention",
                    "title": self._build_notification_title(
                        MentionType(data[8]), notif.author_id
                    ),
                    "content": notif.content_preview or "",
                    "content_preview": notif.content_preview or "",
                    "read": bool(notif.read),
                    "created_at": notif.created_at,
                    "link": self._build_notification_link(
                        notif.server_id, notif.channel_id, notif.message_id
                    ),
                    "sender_id": str(notif.author_id),
                },
            )

        self._queue_push_delivery(results)

        return results
