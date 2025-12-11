"""
Tests for server template functionality.
"""

import pytest


@pytest.mark.servers
class TestTemplateCreation:
    """Tests for creating server templates."""

    def test_create_template_from_server(self, server_with_channels):
        """Test creating a template from an existing server."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="My Template",
            description="A test template",
        )

        assert template is not None
        assert template.name == "My Template"
        assert template.description == "A test template"
        assert template.creator_id == owner.id
        assert template.source_server_id == server.id
        assert template.code is not None
        assert len(template.code) > 0

    def test_create_template_requires_permission(self, server_with_members):
        """Test that creating templates requires permission."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        with pytest.raises(servers.PermissionDeniedError):
            servers.create_template(
                user_id=member_user.id,
                server_id=server.id,
                name="Unauthorized Template",
            )

    def test_create_template_empty_name_fails(self, server_with_members):
        """Test that empty template name fails."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        with pytest.raises(servers.TemplateError):
            servers.create_template(
                user_id=owner.id,
                server_id=server.id,
                name="",
            )

    def test_create_template_generates_unique_code(self, server_with_members):
        """Test that each template gets a unique code."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template1 = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Template 1",
        )

        template2 = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Template 2",
        )

        assert template1.code != template2.code


@pytest.mark.servers
class TestTemplateRetrieval:
    """Tests for retrieving templates."""

    def test_get_template_by_code(self, server_with_members):
        """Test getting a template by code."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Retrievable Template",
        )

        retrieved = servers.get_template(template.code, owner.id)
        assert retrieved is not None
        assert retrieved.id == template.id
        assert retrieved.name == "Retrievable Template"

    def test_get_template_by_id(self, server_with_members):
        """Test getting a template by ID."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="ID Template",
        )

        retrieved = servers.get_template_by_id(template.id, owner.id)
        assert retrieved is not None
        assert retrieved.code == template.code

    def test_get_user_templates(self, server_with_members):
        """Test getting all templates created by a user."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        for i in range(3):
            servers.create_template(
                user_id=owner.id,
                server_id=server.id,
                name=f"User Template {i}",
            )

        templates = servers.get_user_templates(owner.id)
        assert len(templates) >= 3

    def test_get_public_templates(self, server_with_members):
        """Test getting public templates."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Public Template",
        )

        servers.update_template(
            user_id=owner.id,
            code=template.code,
            is_public=True,
        )

        public_templates = servers.get_public_templates()
        public_codes = [t.code for t in public_templates]
        assert template.code in public_codes

    def test_private_template_not_visible_to_others(self, server_with_members):
        """Test that private templates are not visible to other users."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Private Template",
        )

        retrieved = servers.get_template(template.code, member_user.id)
        assert retrieved is None


@pytest.mark.servers
class TestTemplatePreview:
    """Tests for previewing templates."""

    def test_preview_template(self, server_with_channels):
        """Test previewing template data."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Preview Template",
        )

        preview = servers.preview_template(template.code)

        assert preview is not None
        assert isinstance(preview.channels, list)
        assert isinstance(preview.categories, list)
        assert isinstance(preview.roles, list)

    def test_preview_captures_channels(self, server_with_channels):
        """Test that preview captures server channels."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Channel Template",
        )

        preview = servers.preview_template(template.code)
        channel_names = [ch["name"] for ch in preview.channels]

        assert "announcements" in channel_names
        assert "private" in channel_names

    def test_preview_captures_categories(self, server_with_channels):
        """Test that preview captures server categories."""
        server, owner, admin_user, member_user, outsider, general, announcements, private, category, servers = server_with_channels

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Category Template",
        )

        preview = servers.preview_template(template.code)
        category_names = [cat["name"] for cat in preview.categories]

        assert "Text Channels" in category_names

    def test_preview_nonexistent_template_fails(self, server_with_members):
        """Test that previewing nonexistent template fails."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        with pytest.raises(servers.TemplateNotFoundError):
            servers.preview_template("nonexistent_code")


@pytest.mark.servers
class TestTemplateUpdate:
    """Tests for updating templates."""

    def test_update_template_name(self, server_with_members):
        """Test updating template name."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Original Name",
        )

        updated = servers.update_template(
            user_id=owner.id,
            code=template.code,
            name="Updated Name",
        )

        assert updated.name == "Updated Name"

    def test_update_template_description(self, server_with_members):
        """Test updating template description."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Description Template",
        )

        updated = servers.update_template(
            user_id=owner.id,
            code=template.code,
            description="New description",
        )

        assert updated.description == "New description"

    def test_update_template_visibility(self, server_with_members):
        """Test updating template public visibility."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Visibility Template",
        )

        assert template.is_public is False

        updated = servers.update_template(
            user_id=owner.id,
            code=template.code,
            is_public=True,
        )

        assert updated.is_public is True

    def test_update_template_requires_ownership(self, server_with_members):
        """Test that only creator can update template."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Owner Only Template",
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.update_template(
                user_id=admin_user.id,
                code=template.code,
                name="Unauthorized Update",
            )


@pytest.mark.servers
class TestTemplateDeletion:
    """Tests for deleting templates."""

    def test_delete_template(self, server_with_members):
        """Test deleting a template."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="To Delete",
        )

        result = servers.delete_template(owner.id, template.code)
        assert result is True

        deleted = servers.get_template(template.code, owner.id)
        assert deleted is None

    def test_delete_template_requires_ownership(self, server_with_members):
        """Test that only creator can delete template."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = server_with_members

        template = servers.create_template(
            user_id=owner.id,
            server_id=server.id,
            name="Protected Template",
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.delete_template(admin_user.id, template.code)
