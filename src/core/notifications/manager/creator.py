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
        return None


class NotificationCreatorMixin(NotificationProtocol):
    def create_notifications_for_message(
        self,
        message_id: SnowflakeID,
        author_id: Optional[SnowflakeID] = None,
        conversation_id: SnowflakeID = 0,
        content: str = "",
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
        thread_id: Optional[SnowflakeID] = None,
        **kwargs,
    ) -> List[Notification]:
        actual_author_id = author_id or kwargs.get("sender_id")
        if actual_author_id is None:
            raise ValueError("author_id or sender_id must be provided")

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
        if self._presence:
            try:
                presence = self._presence.get_presence(user_id)
                focused_id = getattr(presence, "current_channel_id", None)
                if focused_id and channel_id and int(focused_id) == int(channel_id):
                    return False
            except Exception:
                pass

        if override:
            now = self._get_timestamp()
            is_mute_active = override.muted_until is None or override.muted_until > now
            if override.level == NotificationLevel.MUTED and is_mute_active:
                return False
            if override.level == NotificationLevel.NOTHING:
                return False

        if not settings or settings.level == NotificationLevel.NOTHING:
            return False

        if mention_type in (MentionType.EVERYONE, MentionType.HERE):
            if settings.suppress_everyone:
                return False

        if mention_type == MentionType.ROLE:
            if settings.suppress_roles:
                return False

        return True

    def _should_notify_user(
        self,
        user_id: SnowflakeID,
        author_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        mention_type: MentionType,
    ) -> bool:
        if self._presence:
            try:
                presence = self._presence.get_presence(user_id)
                focused_id = presence.current_channel_id
                if focused_id and channel_id and int(focused_id) == int(channel_id):
                    logger.debug(
                        f"Suppressing notification for user {user_id} - already focused on channel {channel_id}"
                    )
                    return False
            except Exception as e:
                logger.debug(
                    f"Failed to check user focus during notification logic: {e}"
                )

        if channel_id:
            override = self.get_channel_override(user_id, channel_id)
            if override:
                now = self._get_timestamp()
                is_mute_active = (
                    override.muted_until is None or override.muted_until > now
                )
                if override.level == NotificationLevel.MUTED and is_mute_active:
                    return False
                if override.level == NotificationLevel.NOTHING:
                    return False

        settings = self.get_notification_settings(user_id, server_id)

        if settings is None:
            return True

        if settings.level == NotificationLevel.NOTHING:
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

        self._db.execute(
            """INSERT INTO notif_notifications
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
                content_preview,
                content_preview_encrypted,
                created_at,
            ),
        )

        notification = self.get_notification(notif_id)
        if notification:
            from src.api.routes.notifications import _notif_to_response

            self._dispatch_notification_event(
                user_id,
                EventType.NOTIFICATION_CREATE,
                _notif_to_response(notification).model_dump(),
            )

        return notification

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
                    content_preview,
                    content_preview_encrypted,
                    0,
                    created_at,
                )
            )

        self._db.execute_many(
            """INSERT INTO notif_notifications
               (id, user_id, sender_id, message_id, conversation_id, server_id, channel_id, thread_id,
                mention_type, content_preview, content_preview_encrypted, read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            notifications_to_insert,
        )

        for uid in user_ids:
            self._update_unread_count(
                uid, conversation_id, server_id, channel_id, is_mention=True
            )

        results = []
        from src.api.routes.notifications import _notif_to_response

        for data in notifications_to_insert:
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
                content_preview=data[9],
                read=False,
                created_at=data[12],
            )
            results.append(notif)

            self._dispatch_notification_event(
                notif.user_id,
                EventType.NOTIFICATION_CREATE,
                _notif_to_response(notif).model_dump(),
            )

        return results
