"""A recipient's DM privacy setting must gate SENDS into an existing DM.

``create_dm`` checks ``allow_dms_from`` (friends/none) at creation time, but
the send path only checked the (separate) block state.  Every message-send
path -- REST, forward, scheduled dispatch, reaction mentions -- funnels through
``MessageService.send_message``.  Because that path never re-checked the
recipient's ``allow_dms_from``, a party to an existing DM could keep pushing
messages in after the recipient opted out (``none``), or without being a
friend when the recipient requires ``friends``.

These tests are written against the desired fail-closed behaviour and FAIL on
the unfixed code.  Run with:

    pytest src/tests/messaging/test_dm_privacy_send_gate.py -q -n 0
"""

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError


@pytest.fixture
def wired_relationships(db):
    """Expose a real relationships manager, then restore global state."""
    from src.core import relationships

    previous_manager = relationships._manager
    previous_setup = relationships._setup_complete
    relationships.setup(db)
    try:
        yield relationships
    finally:
        relationships._manager = previous_manager
        relationships._setup_complete = previous_setup


@pytest.mark.messaging
class TestDmPrivacySendGate:
    def test_none_setting_denies_send_into_existing_dm(
        self, messaging_manager, wired_relationships, two_users
    ):
        sender, recipient = two_users
        dm = messaging_manager.create_dm(sender.id, recipient.id)
        # Baseline: the DM is writable before the recipient opts out.
        assert messaging_manager.send_message(sender.id, dm.id, "hello") is not None

        settings_svc = messaging_manager._user_settings_svc
        settings_svc.update_message_settings(recipient.id, allow_dms_from="none")

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(sender.id, dm.id, "still there?")

        # The recipient's own side is unaffected by their own setting.
        assert (
            messaging_manager.send_message(recipient.id, dm.id, "my own DM") is not None
        )

    def test_friends_setting_denies_non_friend_send_into_existing_dm(
        self, messaging_manager, wired_relationships, two_users
    ):
        sender, recipient = two_users
        dm = messaging_manager.create_dm(sender.id, recipient.id)

        settings_svc = messaging_manager._user_settings_svc
        settings_svc.update_message_settings(recipient.id, allow_dms_from="friends")

        # They are not friends: sender must no longer be able to write in.
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(sender.id, dm.id, "let me in")

    def test_friends_setting_still_allows_real_friend(
        self, messaging_manager, wired_relationships, two_users
    ):
        """Non-vacuous control: a genuine friend keeps the DM writable."""
        sender, recipient = two_users
        dm = messaging_manager.create_dm(sender.id, recipient.id)

        settings_svc = messaging_manager._user_settings_svc
        settings_svc.update_message_settings(recipient.id, allow_dms_from="friends")

        request = wired_relationships.send_friend_request(sender.id, recipient.id)
        wired_relationships.accept_friend_request(recipient.id, request.id)

        assert messaging_manager.send_message(sender.id, dm.id, "hi friend") is not None

    def test_none_setting_denies_when_relationships_unavailable(
        self, messaging_manager, wired_relationships, two_users, monkeypatch
    ):
        """Fails closed: the setting lives in msg_user_settings, not the optional module."""
        import src.core.messaging.services.message.send as send_service

        sender, recipient = two_users
        dm = messaging_manager.create_dm(sender.id, recipient.id)
        messaging_manager._user_settings_svc.update_message_settings(
            recipient.id, allow_dms_from="none"
        )

        monkeypatch.setattr(send_service, "_get_relationships_module", lambda: None)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(sender.id, dm.id, "bypass attempt")
