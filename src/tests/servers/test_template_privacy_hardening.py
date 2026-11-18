"""Regression tests for private-template read authorization.

A private template must never be returned (or previewed) when the caller's
identity is missing or does not match the creator. Omitting ``user_id`` used
to disable the privacy gate entirely, exposing a template's channel/role
snapshot to anyone who knew its code.
"""

import pytest


@pytest.mark.servers
class TestPrivateTemplateAuthorization:
    def _make_private_template(self, server_with_members, servers):
        server, owner, *_ = server_with_members
        return servers.create_template(owner.id, server.id, "Private Template")

    def test_private_preview_denied_to_other_member(self, server_with_members):
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )
        template = self._make_private_template(server_with_members, servers)

        with pytest.raises(servers.TemplateNotFoundError):
            servers.preview_template(template.code, member_user.id)

    def test_private_preview_denied_without_caller(self, server_with_members):
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )
        template = self._make_private_template(server_with_members, servers)

        with pytest.raises(servers.TemplateNotFoundError):
            servers.preview_template(template.code)

    def test_private_get_template_denied_without_caller(self, server_with_members):
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )
        template = self._make_private_template(server_with_members, servers)

        assert servers.get_template(template.code) is None

    def test_creator_can_preview_private_template(self, server_with_members):
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )
        template = self._make_private_template(server_with_members, servers)

        preview = servers.preview_template(template.code, owner.id)
        assert preview is not None
        assert isinstance(preview.channels, list)

    def test_public_preview_allowed_without_caller(self, server_with_members):
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )
        template = self._make_private_template(server_with_members, servers)
        servers.update_template(owner.id, template.code, is_public=True)

        preview = servers.preview_template(template.code)
        assert preview is not None
