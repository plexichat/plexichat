"""Regression tests: global MUTED level is inherited by DM fan-out.

M2 (audit): ``creator.py`` consulted the per-user ``dm_notifications`` flag
but never fell back to the global muted state. When no per-DM (per-channel)
mute was present, the code treated the recipient as unmuted instead of
inheriting the global MUTED level, so globally-muted users still received
DM notifications.

Repro (pre-fix): set global level=MUTED, DM the user without any override
-> notification still created.

The fix enforces ``settings.level == MUTED`` in
``_should_notify_user_optimized`` (the resolved row for DMs *is* the global
row, and DMs carry no channel override), while preserving explicit
per-channel overrides: an active per-channel mute (or NOTHING) still
suppresses first, and an expired per-channel mute falls through to the
global/per-server mute instead of unmuting.
"""

from unittest.mock import patch

from src.core.notifications.models import (
    ChannelNotificationOverride,
    MentionType,
    NotificationLevel,
)
from src.utils import encryption


def _decide(manager, user_id, author_id, settings, server_id=None, override=None):
    return manager._should_notify_user_optimized(
        user_id=user_id,
        author_id=author_id,
        server_id=server_id,
        channel_id=None,
        mention_type=MentionType.USER,
        settings=settings,
        override=override,
    )


def _override(user_id, level, muted_until=None):
    return ChannelNotificationOverride(
        user_id=user_id,
        channel_id=424242,
        level=level,
        muted_until=muted_until,
    )


class TestDmMutedFallback:
    def test_global_muted_suppresses_dm_decision(self, notification_manager, test_user):
        """Core repro at decision level: global MUTED + DM, no override."""
        notification_manager.update_notification_settings(
            test_user.id, level=NotificationLevel.MUTED
        )
        settings = notification_manager.get_notification_settings(test_user.id)
        assert settings.level == NotificationLevel.MUTED
        assert (
            _decide(notification_manager, test_user.id, test_user.id + 1, settings)
            is False
        )

    def test_global_muted_suppresses_dm_end_to_end(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Finding repro: set global muted, DM without override -> no notif."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            sender = auth_manager.register(
                username="dm_mute_sender",
                email="dm_mute_sender@example.com",
                password="TestPass123!",
            )
            recipient = auth_manager.register(
                username="dm_mute_recipient",
                email="dm_mute_recipient@example.com",
                password="TestPass123!",
            )

        dm = messaging_manager.create_dm(sender.id, recipient.id)

        notification_manager.update_notification_settings(
            recipient.id, level=NotificationLevel.MUTED
        )

        content = f"Hey <@{recipient.id}> hello"
        msg = messaging_manager.send_message(sender.id, dm.id, content)
        notifs = notification_manager.create_notifications_for_message(
            author_id=sender.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert notifs == []
        assert {n.user_id for n in notifs} == set()

    def test_unmuted_user_dm_allowed(self, notification_manager, test_user):
        """Guard against over-suppression: default settings still notify."""
        settings = notification_manager.get_notification_settings(test_user.id)
        assert settings.level == NotificationLevel.ALL_MESSAGES
        assert (
            _decide(notification_manager, test_user.id, test_user.id + 1, settings)
            is True
        )

    def test_active_explicit_override_mute_preserved(
        self, notification_manager, test_user
    ):
        """Explicit per-channel MUTED override still suppresses (indefinite)."""
        settings = notification_manager.get_notification_settings(test_user.id)
        override = _override(test_user.id, NotificationLevel.MUTED, muted_until=None)
        assert (
            _decide(
                notification_manager,
                test_user.id,
                test_user.id + 1,
                settings,
                override=override,
            )
            is False
        )

    def test_explicit_nothing_override_preserved(self, notification_manager, test_user):
        """Explicit per-channel NOTHING override still suppresses."""
        settings = notification_manager.get_notification_settings(test_user.id)
        override = _override(test_user.id, NotificationLevel.NOTHING)
        assert (
            _decide(
                notification_manager,
                test_user.id,
                test_user.id + 1,
                settings,
                override=override,
            )
            is False
        )

    def test_expired_override_inherits_global_mute(
        self, notification_manager, test_user
    ):
        """Expired per-channel mute + global MUTED stays muted (no unmute)."""
        notification_manager.update_notification_settings(
            test_user.id, level=NotificationLevel.MUTED
        )
        settings = notification_manager.get_notification_settings(test_user.id)
        expired = notification_manager._get_timestamp() - 3600000
        override = _override(test_user.id, NotificationLevel.MUTED, muted_until=expired)
        assert (
            _decide(
                notification_manager,
                test_user.id,
                test_user.id + 1,
                settings,
                override=override,
            )
            is False
        )

    def test_expired_override_without_global_mute_allows(
        self, notification_manager, test_user
    ):
        """Expired per-channel mute alone still allows (expiry honored)."""
        settings = notification_manager.get_notification_settings(test_user.id)
        expired = notification_manager._get_timestamp() - 3600000
        override = _override(test_user.id, NotificationLevel.MUTED, muted_until=expired)
        assert (
            _decide(
                notification_manager,
                test_user.id,
                test_user.id + 1,
                settings,
                override=override,
            )
            is True
        )

    def test_dm_preference_off_still_suppresses(self, notification_manager, test_user):
        """Prior M2 fix intact: dm_notifications=False still suppresses DMs."""
        notification_manager.update_notification_settings(
            test_user.id, dm_notifications=False
        )
        settings = notification_manager.get_notification_settings(test_user.id)
        assert settings.dm_notifications is False
        assert settings.level == NotificationLevel.ALL_MESSAGES
        assert (
            _decide(notification_manager, test_user.id, test_user.id + 1, settings)
            is False
        )

    def test_per_server_muted_suppresses_server_mention(
        self, notification_manager, test_user
    ):
        """Same settings-layer enforcement covers per-server MUTED rows."""
        server_id = 987654321
        notification_manager.update_notification_settings(
            test_user.id, server_id=server_id, level=NotificationLevel.MUTED
        )
        settings = notification_manager.get_notification_settings(
            test_user.id, server_id=server_id
        )
        assert settings.level == NotificationLevel.MUTED
        assert (
            _decide(
                notification_manager,
                test_user.id,
                test_user.id + 1,
                settings,
                server_id=server_id,
            )
            is False
        )
