"""Fail-closed authorization when optional dependencies are unwired.

Every manager in ``src.core`` receives its cross-module dependencies
(``servers_module``, ``relationships_module``, ``messaging_module``, ...) by
constructor injection. A recurring audit theme (Agent 21 F2 and the follow-up
sweeps) was the *fail-open* guard: a check that could not be evaluated because
its dependency was ``None`` was silently skipped instead of denied, so an
unwired deployment granted everything.

These tests boot each manager **against a real database but with its optional
dependencies omitted**, then assert the authorization boundary denies. They
are deliberately construction-level end-to-end tests: they exercise the real
manager, real schema, and the real denial path rather than mocking the guard.

The invariant under test:

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The only place fail-open remains intentional is privacy filtering over an
unknowable block graph (reactions/presence block checks) — denying all
reactions there would be a broad outage, so those are excluded.
"""

from unittest.mock import MagicMock

import pytest


# =============================================================================
# Reactions
# =============================================================================


def test_reactions_server_permission_denied_without_servers(db):
    from src.core.reactions.manager import ReactionManager
    from src.core.reactions.exceptions import PermissionDeniedError

    manager = ReactionManager(db)  # servers_module intentionally omitted
    assert manager._check_server_permission(1, 111) is False

    with pytest.raises(PermissionDeniedError):
        manager._require_manage_server(1, 111)


def test_reactions_custom_emoji_management_denied_without_servers(db):
    from src.core.reactions.manager import ReactionManager
    from src.core.reactions.exceptions import PermissionDeniedError

    manager = ReactionManager(db)

    with pytest.raises(PermissionDeniedError):
        manager.create_custom_emoji(1, 111, "wave", b"not-an-image", "image/png")


# =============================================================================
# Voice
# =============================================================================


def test_voice_permission_denied_without_servers(db):
    from src.core.voice.manager import VoiceManager

    manager = VoiceManager(db)  # servers_module intentionally omitted
    assert manager._check_permission(1, 111, "voice.connect") is False

    with pytest.raises(VoiceManager.PermissionDeniedError):
        manager._require_permission(1, 111, "voice.connect")


def test_voice_channel_listing_denied_without_servers(db):
    from src.core.voice.manager import VoiceManager

    manager = VoiceManager(db)
    assert manager.get_voice_channels(1, 111) == []
    assert manager.get_voice_channel(222, 1) is None


# =============================================================================
# Threads
# =============================================================================


def test_threads_permission_denied_without_servers(db):
    from src.core.threads.manager import ThreadManager
    from src.core.threads.exceptions import PermissionDeniedError

    manager = ThreadManager(db)  # servers_module intentionally omitted
    assert manager._check_permission(1, 111, "channels.view") is False

    with pytest.raises(PermissionDeniedError):
        manager._require_permission(1, 111, "channels.view")


# =============================================================================
# Webhooks
# =============================================================================


def test_webhooks_manage_permission_denied_without_servers(db):
    from src.core.webhooks.manager import WebhookManager

    manager = WebhookManager(db)  # servers_module intentionally omitted
    assert manager._check_manage_webhooks_permission(1, 111) is False


# =============================================================================
# Soundboard
# =============================================================================


def test_soundboard_permission_and_membership_denied_without_servers(db):
    from src.core.soundboard.manager import SoundboardManager
    from src.core.soundboard.exceptions import PermissionDeniedError
    from src.core.soundboard.models import SoundFormat

    manager = SoundboardManager(db)  # servers_module intentionally omitted
    assert manager._check_server_permission(1, 111) is False
    assert manager._is_server_member(1, 111) is False

    with pytest.raises(PermissionDeniedError):
        manager.upload_sound(
            user_id=1,
            server_id=111,
            name="airhorn",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/airhorn.mp3",
            size=1024,
            duration_seconds=1.0,
        )


# =============================================================================
# Stickers
# =============================================================================


def test_stickers_permission_and_membership_denied_without_servers(db):
    from src.core.stickers.manager import StickerManager

    manager = StickerManager(db)  # servers_module intentionally omitted
    assert manager._check_server_permission(1, 111) is False
    assert manager._is_server_member(1, 111) is False


def test_stickers_server_scoped_suggestions_denied_without_servers(db):
    from src.core.stickers.manager import StickerManager

    manager = StickerManager(db)
    # A caller asking for another server's pack must not be able to enumerate
    # it when membership cannot be verified.
    assert manager.get_sticker_suggestions(1, "hello", server_id=111) == []


# =============================================================================
# Applications (bot approval)
# =============================================================================


def test_applications_bot_approval_denied_without_servers(db):
    from src.core.applications.manager import ApplicationManager
    from src.core.applications.exceptions import PermissionDeniedError

    manager = ApplicationManager(db)  # servers_module intentionally omitted
    with pytest.raises(PermissionDeniedError):
        manager.approve_bot(server_id=111, application_id=222, approved_by=1)


# =============================================================================
# Search discovery
# =============================================================================


def test_search_discovery_list_and_bump_denied_without_servers(db):
    from src.core.search.discovery import DiscoveryManager
    from src.core.search.exceptions import SearchPermissionError

    manager = DiscoveryManager(db)  # servers_module intentionally omitted
    with pytest.raises(SearchPermissionError):
        manager._check_list_permission(1, 111)
    with pytest.raises(SearchPermissionError):
        manager._check_bump_permission(1, 111)


# =============================================================================
# PlexiSuite (Sum / Stash)
# =============================================================================


def test_suite_sum_membership_denied_without_servers(db, tmp_path):
    from src.core.suite.sum.manager import SumManager

    manager = SumManager(db, servers_module=None, storage_dir=str(tmp_path))
    assert manager._is_member(1, 111) is False


def test_suite_stash_membership_denied_without_servers(db):
    from src.core.suite.stash.manager import StashManager

    manager = StashManager(db)  # servers_module intentionally omitted
    assert manager._is_member(1, 111) is False


# =============================================================================
# Presence
# =============================================================================


def test_presence_online_queries_denied_without_dependencies(db):
    from src.core.presence.manager import PresenceManager

    manager = PresenceManager(db)  # relationships/servers omitted
    assert manager.get_online_friends(1) == []
    assert manager.get_online_server_members(1, 111) == []


# =============================================================================
# Notifications (@everyone / @here)
# =============================================================================


def test_notifications_everyone_mention_denied_without_servers(db):
    from src.core.notifications.manager import NotificationManager
    from src.core.notifications.models import Mention, MentionType

    manager = NotificationManager(db)  # servers_module intentionally omitted
    result = manager.validate_mentions(
        1,
        [Mention(mention_type=MentionType.EVERYONE, raw_text="@everyone")],
        server_id=111,
        channel_id=222,
    )
    assert result and result[0].valid is False


# =============================================================================
# AutoMod actions
# =============================================================================


def test_automod_kick_ban_timeout_refused_without_servers(db):
    from src.core.automod.actions.kick import KickUserAction
    from src.core.automod.actions.ban import BanUserAction
    from src.core.automod.actions.timeout import TimeoutUserAction

    action = MagicMock()
    violation = MagicMock()
    violation.server_id = 111
    violation.user_id = 1

    assert KickUserAction(db).execute(action, violation) is False
    assert BanUserAction(db).execute(action, violation) is False
    assert TimeoutUserAction(db).execute(action, violation) is False


def test_notifications_mention_everyone_helper_denied_without_servers(db):
    """The @everyone helper itself must fail closed, not just its caller.

    ``has_mention_everyone_permission`` returned True when the servers module
    was unwired, contradicting the documented policy in
    ``MentionValidationMixin``. The current caller already denies earlier, so
    this pins the helper's own contract for future callers.
    """
    from src.core.notifications.manager.helpers import has_mention_everyone_permission

    assert has_mention_everyone_permission(None, 1, 111, 222) is False
    assert has_mention_everyone_permission(False, 1, 111) is False

    class _Servers:
        def has_permission(self, user_id, server_id, permission, channel_id=None):
            return permission == "messages.mention_everyone"

    assert has_mention_everyone_permission(_Servers(), 1, 111, 222) is True


def test_automod_message_delete_refused_without_messaging(db):
    from src.core.automod.actions.delete import DeleteMessageAction

    action = MagicMock()
    violation = MagicMock()
    violation.message_id = 333

    assert DeleteMessageAction(db).execute(action, violation) is False
