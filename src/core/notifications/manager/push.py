from ..models import Notification, PushPayload, MentionType


from .protocol import NotificationProtocol


class PushMixin(NotificationProtocol):
    def prepare_push_payload(self, notification: Notification) -> PushPayload:
        sender_name = "Someone"
        row = self._db.fetch_one(
            "SELECT username FROM auth_users WHERE id = ?", (notification.author_id,)
        )
        if row:
            sender_name = row["username"]

        if notification.mention_type == MentionType.USER:
            title = f"{sender_name} mentioned you"
        elif notification.mention_type == MentionType.ROLE:
            title = f"{sender_name} mentioned your role"
        elif notification.mention_type == MentionType.EVERYONE:
            title = f"{sender_name} mentioned @everyone"
        elif notification.mention_type == MentionType.HERE:
            title = f"{sender_name} mentioned @here"
        else:
            title = f"New mention from {sender_name}"

        unread = self.get_unread_count(notification.user_id)

        return PushPayload(
            user_id=notification.user_id,
            title=title,
            body=notification.content_preview,
            data={
                "notification_id": notification.id,
                "message_id": notification.message_id,
                "conversation_id": notification.conversation_id,
                "server_id": notification.server_id,
                "channel_id": notification.channel_id,
                "thread_id": notification.thread_id,
                "mention_type": notification.mention_type.value,
            },
            badge_count=unread.mention_count,
            sound="default",
            priority="high",
        )
