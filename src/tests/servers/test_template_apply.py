"""
Tests for applying server templates.
"""

import pytest
import uuid


@pytest.mark.servers
class TestTemplateApplication:
    """Tests for applying templates to create new servers."""

    def test_apply_template_creates_server(self, server_with_channels):
        """Test that applying a template creates a new server."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Apply Test Template",
        )

        unique_name = f"New Server {uuid.uuid4().hex[:6]}"
        new_server = servers.apply_template(
            user_id=owner.id,
            code=template.code,
            server_name=unique_name,
            server_description="Created from template",
        )

        assert new_server is not None
        assert new_server.name == unique_name
        assert new_server.description == "Created from template"
        assert new_server.owner_id == owner.id

    def test_apply_template_creates_channels(self, server_with_channels):
        """Test that applying a template creates channels."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Channel Template",
        )

        unique_name = f"Channel Server {uuid.uuid4().hex[:6]}"
        new_server = servers.apply_template(
            user_id=owner.id,
            code=template.code,
            server_name=unique_name,
        )

        channels = servers.get_channels(owner.id, new_server.id)
        channel_names = [ch.name for ch in channels]

        assert "announcements" in channel_names
        assert "private" in channel_names

    def test_apply_template_creates_categories(self, server_with_channels):
        """Test that applying a template creates categories."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Category Template",
        )

        unique_name = f"Category Server {uuid.uuid4().hex[:6]}"
        new_server = servers.apply_template(
            user_id=owner.id,
            code=template.code,
            server_name=unique_name,
        )

        channels = servers.get_channels(owner.id, new_server.id)
        channels_with_category = [ch for ch in channels if ch.category_id is not None]

        assert len(channels_with_category) > 0

    def test_apply_template_creates_roles(self, server_with_members):
        """Test that applying a template creates roles."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        mod_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Moderator",
            permissions={"messages.manage": True},
            color="#00FF00",
        )

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Role Template",
        )

        unique_name = f"Role Server {uuid.uuid4().hex[:6]}"
        new_server = servers.apply_template(
            user_id=owner.id,
            code=template.code,
            server_name=unique_name,
        )

        roles = servers.get_roles(owner.id, new_server.id)
        role_names = [r.name for r in roles]

        assert "Admin" in role_names
        assert "Moderator" in role_names

    def test_apply_template_increments_usage_count(self, server_with_members):
        """Test that applying a template increments usage count."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Usage Count Template",
        )

        initial_count = template.usage_count

        unique_name = f"Usage Server {uuid.uuid4().hex[:6]}"
        servers.apply_template(
            user_id=owner.id,
            code=template.code,
            server_name=unique_name,
        )

        updated_template = servers.get_template(template.code, owner.id)
        assert updated_template.usage_count == initial_count + 1

    def test_apply_nonexistent_template_fails(self, server_with_members):
        """Test that applying nonexistent template fails."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        with pytest.raises(servers.TemplateNotFoundError):
            servers.apply_template(
                user_id=owner.id,
                code="nonexistent_code",
                server_name="Failed Server",
            )

    def test_apply_public_template_by_other_user(self, server_with_members):
        """Test that other users can apply public templates."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Public Apply Template",
        )

        servers.update_template(
            user_id=owner.id,
            code=template.code,
            is_public=True,
        )

        unique_name = f"Other User Server {uuid.uuid4().hex[:6]}"
        new_server = servers.apply_template(
            user_id=outsider.id,
            code=template.code,
            server_name=unique_name,
        )

        assert new_server is not None
        assert new_server.owner_id == outsider.id


@pytest.mark.servers
class TestTemplateChannelPreservation:
    """Tests for channel property preservation in templates."""

    def test_template_preserves_channel_type(self, server_with_channels):
        """Test that template preserves channel types."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        voice_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="voice-room",
            channel_type=servers.ChannelType.VOICE,
        )

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Type Template",
        )

        unique_name = f"Type Server {uuid.uuid4().hex[:6]}"
        new_server = servers.apply_template(
            user_id=owner.id,
            code=template.code,
            server_name=unique_name,
        )

        channels = servers.get_channels(owner.id, new_server.id)
        voice_channels = [ch for ch in channels if ch.channel_type == servers.ChannelType.VOICE]

        assert len(voice_channels) > 0

    def test_template_preserves_channel_settings(self, server_with_channels):
        """Test that template preserves channel settings like NSFW and slowmode."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        nsfw_channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="nsfw-channel",
            nsfw=True,
            slowmode_seconds=30,
        )

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Settings Template",
        )

        preview = servers.preview_template(template.code)
        nsfw_channels = [ch for ch in preview.channels if ch.get("nsfw")]

        assert len(nsfw_channels) > 0
        nsfw_ch = next(ch for ch in preview.channels if ch["name"] == "nsfw-channel")
        assert nsfw_ch["slowmode_seconds"] == 30


@pytest.mark.servers
class TestTemplateRolePreservation:
    """Tests for role property preservation in templates."""

    def test_template_preserves_role_permissions(self, server_with_members):
        """Test that template preserves role permissions."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        custom_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Custom Role",
            permissions={
                "messages.manage": True,
                "members.kick": True,
            },
        )

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Permission Template",
        )

        preview = servers.preview_template(template.code)
        custom_roles = [r for r in preview.roles if r["name"] == "Custom Role"]

        assert len(custom_roles) == 1
        assert custom_roles[0]["permissions"].get("messages.manage") is True
        assert custom_roles[0]["permissions"].get("members.kick") is True

    def test_template_preserves_role_appearance(self, server_with_members):
        """Test that template preserves role color and hoist settings."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        styled_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Styled Role",
            color="#FF5500",
            hoist=True,
            mentionable=True,
        )

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Style Template",
        )

        preview = servers.preview_template(template.code)
        styled_roles = [r for r in preview.roles if r["name"] == "Styled Role"]

        assert len(styled_roles) == 1
        assert styled_roles[0]["color"] == "#FF5500"
        assert styled_roles[0]["hoist"] is True
        assert styled_roles[0]["mentionable"] is True

    def test_template_excludes_default_role(self, server_with_members):
        """Test that template excludes the @everyone default role."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="No Default Template",
        )

        preview = servers.preview_template(template.code)
        default_roles = [r for r in preview.roles if r["name"] == "@everyone"]

        assert len(default_roles) == 0
