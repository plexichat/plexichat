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
    get_role_members,
    get_server_members,
    get_online_members,
    truncate_content,
)


from .protocol import NotificationProtocol


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
                role_members = get_role_members(self._db, mention.target_id)
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

        now = self._get_timestamp()
        preview = truncate_content(content, self._config)

        user_ids_list = list(users_to_notify)
        all_settings = self.get_notification_settings_bulk(user_ids_list, server_id)
        all_overrides = {}
        if channel_id:
            all_overrides = self.get_channel_overrides_bulk(user_ids_list, channel_id)

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

    def _should_notify_user_optimized(
        self,
        user_id: SnowflakeID,
        author_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        mention_type: MentionType,
        settings: Optional["NotificationSettings"],
        override: Optional["ChannelNotificationOverride"],
    ) -> bool:
        # Check if user is currently focused on this channel
        if self._presence:
            try:
                presence = self._presence.get_presence(user_id)
                focused_id = getattr(presence, "current_channel_id", None)
                if focused_id and channel_id and int(focused_id) == int(channel_id):
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
        content_preview_encrypted = _encrypt_content_preview(content_preview, notif_id)

        if content_preview_encrypted and content_preview_encrypted != content_preview:
            content_preview_to_store = ""
        else:
            content_preview_to_store = content_preview
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

        notification = self.get_notification(notif_id)
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

            for uid in user_ids:
                self._update_unread_count(
                    uid, conversation_id, server_id, channel_id, is_mention=True
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
                except Exception:
                    pass

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
