"""
Tests for audit log operations.
"""

import pytest


class TestGetAuditLog:
    """Tests for getting audit log."""

    def test_get_audit_log_success(self, fresh_server):
        """Test getting audit log."""
        server, owner, servers = fresh_server

        entries = servers.get_audit_log(owner.id, server.id)

        assert isinstance(entries, list)
        # Should have at least server_create entry
        assert len(entries) >= 1

    def test_audit_log_includes_server_create(self, fresh_server):
        """Test that server creation is logged."""
        server, owner, servers = fresh_server

        entries = servers.get_audit_log(owner.id, server.id)

        create_entries = [e for e in entries if e.action == servers.AuditLogAction.SERVER_CREATE]
        assert len(create_entries) >= 1

    def test_audit_log_includes_channel_create(self, server_with_channels):
        """Test that channel creation is logged."""
        server, owner, _, _, _, _, _, _, _, servers = server_with_channels

        entries = servers.get_audit_log(owner.id, server.id)

        channel_entries = [e for e in entries if e.action == servers.AuditLogAction.CHANNEL_CREATE]
        assert len(channel_entries) >= 1

    def test_audit_log_includes_role_create(self, server_with_members):
        """Test that role creation is logged."""
        server, owner, _, _, _, _, servers = server_with_members

        entries = servers.get_audit_log(owner.id, server.id)

        role_entries = [e for e in entries if e.action == servers.AuditLogAction.ROLE_CREATE]
        assert len(role_entries) >= 1

    def test_audit_log_includes_member_join(self, server_with_members):
        """Test that member join is logged."""
        server, owner, _, _, _, _, servers = server_with_members

        entries = servers.get_audit_log(owner.id, server.id)

        join_entries = [e for e in entries if e.action == servers.AuditLogAction.MEMBER_JOIN]
        assert len(join_entries) >= 1

    def test_audit_log_filter_by_action(self, server_with_members):
        """Test filtering audit log by action type."""
        server, owner, _, _, _, _, servers = server_with_members

        entries = servers.get_audit_log(
            owner.id, server.id,
            action_type=servers.AuditLogAction.MEMBER_JOIN
        )

        assert all(e.action == servers.AuditLogAction.MEMBER_JOIN for e in entries)

    def test_audit_log_respects_limit(self, server_with_members):
        """Test that limit is respected."""
        server, owner, _, _, _, _, servers = server_with_members

        entries = servers.get_audit_log(owner.id, server.id, limit=2)

        assert len(entries) <= 2

    def test_audit_log_without_permission_fails(self, server_with_members):
        """Test that getting audit log without permission fails."""
        server, _, _, member_user, _, _, servers = server_with_members

        with pytest.raises(servers.PermissionDeniedError):
            servers.get_audit_log(member_user.id, server.id)

    def test_audit_log_includes_changes(self, fresh_server):
        """Test that audit log includes changes."""
        server, owner, servers = fresh_server

        # Update server to create an entry with changes
        servers.update_server(owner.id, server.id, name="Updated Name")

        entries = servers.get_audit_log(owner.id, server.id)

        update_entries = [e for e in entries if e.action == servers.AuditLogAction.SERVER_UPDATE]
        assert len(update_entries) >= 1
        assert update_entries[0].changes is not None

    def test_audit_log_includes_reason(self, server_with_members):
        """Test that audit log includes reason for kicks/bans."""
        server, owner, _, member_user, _, _, servers = server_with_members

        servers.kick_member(owner.id, server.id, member_user.id, reason="Test kick")

        entries = servers.get_audit_log(owner.id, server.id)

        kick_entries = [e for e in entries if e.action == servers.AuditLogAction.MEMBER_KICK]
        assert len(kick_entries) >= 1
        assert kick_entries[0].reason == "Test kick"


class TestAuditLogEntry:
    """Tests for audit log entry structure."""

    def test_entry_has_required_fields(self, fresh_server):
        """Test that entry has all required fields."""
        server, owner, servers = fresh_server

        entries = servers.get_audit_log(owner.id, server.id)

        assert len(entries) >= 1
        entry = entries[0]

        assert entry.id is not None
        assert entry.server_id == server.id
        assert entry.user_id is not None
        assert entry.action is not None
        assert entry.created_at is not None

    def test_entry_target_info(self, server_with_channels):
        """Test that entry includes target info."""
        server, owner, _, _, _, general, _, _, _, servers = server_with_channels

        entries = servers.get_audit_log(owner.id, server.id)

        channel_entries = [e for e in entries if e.action == servers.AuditLogAction.CHANNEL_CREATE]
        assert len(channel_entries) >= 1

        entry = channel_entries[0]
        assert entry.target_type == "channel"
        assert entry.target_id is not None
