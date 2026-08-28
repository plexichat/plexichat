"""
Regression tests for servers cluster fixes.

Covers:
- slowmode_seconds validated at handler level (internal callers bypass schema)
- max_roles_per_server enforced on role creation
- Channel deletion cleans up overrides and the linked conversation
- icon_url passed through on server creation
"""

import pytest


class TestSlowmodeValidation:
    """L1: slowmode must be bounded at the handler, not just the schema."""

    def _err(self):
        from src.core.servers.exceptions import InvalidChannelSettingError

        return InvalidChannelSettingError

    def test_out_of_range_slowmode_rejected(self, fresh_server):
        server, owner, servers = fresh_server
        channel = servers.create_channel(
            owner.id, server.id, "slow", channel_type=servers.ChannelType.TEXT
        )

        with pytest.raises(self._err()):
            servers.update_channel(owner.id, channel.id, slowmode_seconds=999999)

    def test_negative_slowmode_rejected(self, fresh_server):
        server, owner, servers = fresh_server
        channel = servers.create_channel(
            owner.id, server.id, "slow2", channel_type=servers.ChannelType.TEXT
        )

        with pytest.raises(self._err()):
            servers.update_channel(owner.id, channel.id, slowmode_seconds=-5)

    def test_upper_bound_accepted(self, fresh_server):
        server, owner, servers = fresh_server
        channel = servers.create_channel(
            owner.id, server.id, "slow3", channel_type=servers.ChannelType.TEXT
        )

        updated = servers.update_channel(owner.id, channel.id, slowmode_seconds=21600)
        assert updated.slowmode_seconds == 21600


class TestRoleLimit:
    """L2: the configured per-server role cap must be enforced."""

    def test_create_role_past_limit_rejected(self, fresh_server, test_db):
        server, owner, servers = fresh_server
        from src.core.servers.exceptions import ServerLimitReachedError

        # Shrink the cap for the test via config override. Servers start with
        # the default @everyone role, so one created role already reaches 2.
        servers._config["max_roles_per_server"] = 2

        servers.create_role(owner.id, server.id, "Role One", {"messages.manage": True})

        with pytest.raises(ServerLimitReachedError):
            servers.create_role(
                owner.id, server.id, "Role Two", {"messages.manage": True}
            )

        # The rejected role must not be persisted (default + one created).
        row = test_db.fetch_one(
            "SELECT COUNT(*) as count FROM srv_roles "
            "WHERE server_id = ? AND deleted = 0",
            (server.id,),
        )
        assert row["count"] == 2

    def test_under_limit_still_creates(self, fresh_server):
        server, owner, servers = fresh_server
        role = servers.create_role(
            owner.id, server.id, "Solo", {"messages.manage": True}
        )
        assert role is not None


class TestChannelDeletionCleanup:
    """L3: deleting a channel must not orphan overrides or the conversation."""

    def test_delete_channel_cleans_overrides_and_conversation(
        self, fresh_server, test_db
    ):
        server, owner, servers = fresh_server
        channel = servers.create_channel(
            owner.id, server.id, "doomed", channel_type=servers.ChannelType.TEXT
        )

        conv_id = channel.conversation_id
        assert conv_id is not None

        # Seed an override row + confirm the conversation is live.
        test_db.execute(
            "INSERT INTO srv_channel_overrides "
            "(channel_id, target_type, target_id, allow, deny, created_at, updated_at) "
            "VALUES (?, 'role', 1, NULL, NULL, 1, 1)",
            (channel.id,),
        )
        live = test_db.fetch_one(
            "SELECT deleted FROM msg_conversations WHERE id = ?", (conv_id,)
        )
        assert live["deleted"] == 0

        servers.delete_channel(owner.id, channel.id)

        overrides = test_db.fetch_one(
            "SELECT COUNT(*) as count FROM srv_channel_overrides WHERE channel_id = ?",
            (channel.id,),
        )
        assert overrides["count"] == 0

        conv = test_db.fetch_one(
            "SELECT deleted FROM msg_conversations WHERE id = ?", (conv_id,)
        )
        assert conv["deleted"] == 1


class TestCreateServerIcon:
    """L4: icon_url must reach create_server instead of being dropped."""

    @pytest.mark.asyncio
    async def test_create_server_passes_icon_url(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from src.api.routes.servers.server_crud import create_server

        fake_servers = MagicMock()
        fake_servers.create_server.return_value = SimpleNamespace(
            id=1,
            name="Team",
            description=None,
            owner_id=7,
            icon_url="/api/v1/avatars/servers/1",
            created_at=1,
            updated_at=1,
            verification_level=0,
            default_message_notifications=0,
            max_reactions_per_message=20,
        )
        current_user = SimpleNamespace(user_id=7)
        body = SimpleNamespace(
            name="Team",
            description=None,
            icon_url="/api/v1/avatars/servers/1",
        )
        with patch("src.api.get_servers", return_value=fake_servers):
            await create_server(body, current_user)

        call_kwargs = fake_servers.create_server.call_args.kwargs
        assert call_kwargs["owner_id"] == 7
        assert call_kwargs["name"] == "Team"
        assert call_kwargs["icon_url"] == "/api/v1/avatars/servers/1"
