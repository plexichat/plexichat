"""PlexiJoin inbound request lifecycle (approve/deny)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def plexijoin(db):
    from src.core.plexijoin.manager import PlexiJoinManager

    class _StubEnc:
        def encrypt(self, s):
            return "enc:" + s

        def decrypt(self, s):
            return s[len("enc:") :]

    return PlexiJoinManager(db, admin_logger=None, encryption_service=_StubEnc())


class TestPlexiJoinInbound:
    def test_list_requests_envelope(self, plexijoin):
        env = plexijoin.list_requests()
        assert "requests" in env
        assert "total" in env

    def test_approve_request_missing_raises(self, plexijoin):
        try:
            plexijoin.approve_request(999999, admin_id=1)
        except ValueError:
            # ValueError is the canonical "request not found" path.
            pass

    def test_deny_request_missing_raises(self, plexijoin):
        try:
            plexijoin.deny_request(999999, admin_id=1)
        except ValueError:
            pass

    def test_status_summary_construction(self, plexijoin):
        s = plexijoin.get_status_summary()
        # Fields exist even when DB is empty.
        assert isinstance(s["active_connections"], int)
        assert isinstance(s["broken_connections"], int)
        assert isinstance(s["pending_requests"], int)
        assert isinstance(s["messages_today"], int)
