"""Task 12: IDOR hardening for server/channel access boundaries."""

import pytest


@pytest.mark.integration
class TestServerChannelIdor:
    def test_get_channel_denied_to_non_member(self, fresh_server_tuple):
        server, owner, servers = fresh_server_tuple
        channel = servers.create_channel(owner.id, server.id, "idor-chan")
        outsider = servers._auth.register(
            username="idor_srv_nm",
            email="idor_srv_nm@example.com",
            password="TestPass123!",
        )
        assert servers.get_channel(channel.id, outsider.id) is None

    def test_get_permissions_denied_to_non_member(self, fresh_server_tuple):
        server, owner, servers = fresh_server_tuple
        outsider = servers._auth.register(
            username="idor_srv_perm",
            email="idor_srv_perm@example.com",
            password="TestPass123!",
        )
        # A non-member must not resolve effective permissions as if they belong.
        perms = servers.get_permissions(outsider.id, server.id)
        assert perms == {} or perms.get("channels.view") is not True

    def test_channels_view_override_deny_blocks_member(self, server_with_channels):
        (
            server,
            owner,
            _admin,
            member_user,
            _mod,
            general,
            _ann,
            _staff,
            _cat,
            servers,
        ) = server_with_channels
        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            deny={"channels.view": True},
        )
        assert (
            servers.has_permission(
                member_user.id, server.id, "channels.view", general.id
            )
            is False
        )
        channels = servers.get_channels(member_user.id, server.id)
        assert all(c.id != general.id for c in channels)

    def test_channels_view_required_to_receive_via_permission_check(
        self, server_with_channels
    ):
        server, owner, _admin, member_user, _mod, general, *_rest = server_with_channels
        servers = _rest[-1]
        assert (
            servers.has_permission(
                member_user.id, server.id, "channels.view", general.id
            )
            is True
        )
        servers.set_channel_override(
            user_id=owner.id,
            channel_id=general.id,
            target_type="member",
            target_id=member_user.id,
            deny={"channels.view": True},
        )
        assert (
            servers.has_permission(
                member_user.id, server.id, "channels.view", general.id
            )
            is False
        )
