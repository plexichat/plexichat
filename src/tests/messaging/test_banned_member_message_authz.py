"""Regression: a banned server member must lose edit/delete rights on messages
posted in that server's channels.

``add_member`` seeds each channel conversation with a direct participant row,
so the messaging membership oracle short-circuits on that row and never
re-checks server membership.  ``ban_member`` removed the srv_members row but,
unlike kick/leave, never removed those participant rows.  A banned member
therefore kept editing (and, for a channel creator/OWNER participant, deleting
other users' messages) inside a server they could no longer access.

Run with: pytest src/tests/messaging/test_banned_member_message_authz.py -q -n 0
"""

import pytest

from src.core.messaging.exceptions import MessageNotFoundError


@pytest.fixture
def banned_member_ctx(modules, user_pool):
    owner = user_pool.get_user()
    member = user_pool.get_user()
    servers = modules.servers
    messaging = modules.messaging

    server = servers.create_server(owner_id=owner.id, name="Ban authz server")
    servers.add_member(server.id, member.id)
    channels = servers.get_channels(owner.id, server.id)
    channel = next(c for c in channels if getattr(c, "conversation_id", None))
    conv_id = int(channel.conversation_id)

    msg = messaging.send_message(member.id, conv_id, "before ban")
    assert msg is not None

    # The owner bans the member.  From here on the member has no server access.
    servers.ban_member(owner.id, server.id, member.id)

    return messaging, server, conv_id, member, owner, msg


@pytest.mark.messaging
class TestBannedMemberMessageAuthorization:
    def test_banned_member_is_not_a_participant(self, banned_member_ctx):
        messaging, server, conv_id, member, owner, msg = banned_member_ctx
        assert messaging.is_participant(conv_id, member.id) is False

    def test_banned_member_cannot_edit_own_message(self, banned_member_ctx):
        messaging, server, conv_id, member, owner, msg = banned_member_ctx
        with pytest.raises(MessageNotFoundError):
            messaging.edit_message(member.id, msg.id, "after ban")

    def test_banned_member_cannot_delete_own_message(self, banned_member_ctx):
        messaging, server, conv_id, member, owner, msg = banned_member_ctx
        with pytest.raises(MessageNotFoundError):
            messaging.delete_message(member.id, msg.id)

    def test_banned_member_cannot_bulk_delete_message(self, banned_member_ctx):
        messaging, server, conv_id, member, owner, msg = banned_member_ctx
        assert messaging.delete_messages_bulk(member.id, conv_id, [msg.id]) == []
