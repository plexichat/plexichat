"""Regression tests: servers listing ``limit`` must be clamped from below.

``get_members`` / ``get_servers`` / ``get_audit_log`` (both the manager
mixin and the ``AuditHandler``) capped ``limit`` from above
(``min(limit, N)``) but never from below. A negative ``limit`` reached the
SQL ``LIMIT ?`` verbatim, and SQLite treats ``LIMIT -1`` as *unlimited* —
so ``limit=-1`` dumped the whole member / server / audit-log list instead
of an empty page.
"""

import pytest


@pytest.mark.servers
class TestServersNegativeLimitClamp:
    def test_get_members_negative_limit_returns_empty(self, server_with_members):
        """A negative member-list limit must not bypass the page cap."""
        server, owner, _admin, _member, _outsider, _role, servers = server_with_members
        assert servers.get_members(owner.id, server.id, limit=-1) == []
        # A clamped negative limit behaves exactly like limit=0.
        assert servers.get_members(owner.id, server.id, limit=0) == []
        # Positive limits still work.
        assert len(servers.get_members(owner.id, server.id, limit=10)) == 3

    def test_get_servers_negative_limit_returns_empty(self, fresh_server):
        """A negative server-list limit must not bypass the page cap."""
        server, owner, servers = fresh_server
        assert servers.get_servers(owner.id, limit=-1) == []
        assert servers.get_servers(owner.id, limit=0) == []
        assert len(servers.get_servers(owner.id, limit=10)) >= 1

    def test_get_audit_log_negative_limit_returns_empty(self, fresh_server):
        """A negative audit-log limit must not dump the whole log."""
        server, owner, servers = fresh_server
        assert servers.get_audit_log(owner.id, server.id, limit=-1) == []
        assert servers.get_audit_log(owner.id, server.id, limit=0) == []
        assert len(servers.get_audit_log(owner.id, server.id, limit=10)) >= 1

    def test_audit_handler_negative_limit_returns_empty(self, fresh_server):
        """The AuditHandler path has the same clamp requirement."""
        server, owner, servers = fresh_server
        handler = servers.audit_handler
        assert handler.get_audit_log(server.id, limit=-1) == []
        assert handler.get_audit_log(server.id, limit=0) == []
        assert len(handler.get_audit_log(server.id, limit=10)) >= 1
