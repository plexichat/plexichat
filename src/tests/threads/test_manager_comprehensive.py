"""Comprehensive Threads tests targeting 80%+ coverage."""

import pytest
from unittest.mock import Mock
from src.core.threads.models import ThreadType, ThreadState
from src.core.threads.exceptions import (
    ThreadNameError,
    PermissionDeniedError,
    ChannelNotFoundError,
    ThreadMemberExistsError,
    ThreadMemberNotFoundError,
    ThreadLockedError,
)


class TestThreadErrors:
    def test_invalid_name_empty(self, thread_manager):
        """Thread name cannot be empty."""
        with pytest.raises(ThreadNameError):
            thread_manager._validate_thread_name("")

    def test_invalid_name_too_long(self, thread_manager):
        """Thread name too long."""
        with pytest.raises(ThreadNameError):
            thread_manager._validate_thread_name("x" * 200)

    def test_create_thread_no_permission(self, thread_manager, test_db, monkeypatch):
        """Need permission to create thread."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )

        mock_servers = Mock()
        mock_servers.has_permission = Mock(return_value=False)
        monkeypatch.setattr(thread_manager, "_servers", mock_servers)

        with pytest.raises(PermissionDeniedError):
            thread_manager.create_thread(2, 1, "Test Thread")

    def test_create_thread_channel_not_found(self, thread_manager):
        """Cannot create thread in nonexistent channel."""
        with pytest.raises(ChannelNotFoundError):
            thread_manager.create_thread(1, 99999, "Test Thread")

    def test_join_thread_already_member(self, thread_manager, test_db):
        """Cannot join twice."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )

        thread = thread_manager.create_thread(1, 1, "Test Thread")

        with pytest.raises(ThreadMemberExistsError):
            thread_manager.join_thread(1, thread.id)

    def test_leave_thread_not_member(self, thread_manager, test_db):
        """Cannot leave if not member."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )

        thread = thread_manager.create_thread(1, 1, "Test Thread")

        with pytest.raises(ThreadMemberNotFoundError):
            thread_manager.leave_thread(2, thread.id)

    def test_add_member_to_private_no_permission(self, thread_manager, test_db):
        """Cannot add to private thread without permission."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )
        test_db.execute(
            "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (1, 1, 1, 1000, 1000), (2, 1, 2, 1000, 1000), (3, 1, 3, 1000, 1000)"
        )

        thread = thread_manager.create_thread(1, 1, "Private", ThreadType.PRIVATE)
        thread_manager.add_member(1, thread.id, 2)

        with pytest.raises(PermissionDeniedError):
            thread_manager.add_member(2, thread.id, 3)

    def test_remove_member_not_admin(self, thread_manager, test_db):
        """Cannot remove member without permission."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )
        test_db.execute(
            "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (1, 1, 1, 1000, 1000), (2, 1, 2, 1000, 1000), (3, 1, 3, 1000, 1000)"
        )

        thread = thread_manager.create_thread(1, 1, "Test")
        thread_manager.join_thread(2, thread.id)
        thread_manager.join_thread(3, thread.id)

        with pytest.raises(PermissionDeniedError):
            thread_manager.remove_member(2, thread.id, 3)

    def test_archive_thread(self, thread_manager, test_db):
        """Can archive thread."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )

        thread = thread_manager.create_thread(1, 1, "Test")

        archived = thread_manager.archive_thread(1, thread.id)
        assert archived.state == ThreadState.ARCHIVED

    def test_archive_thread_no_permission(self, thread_manager, test_db):
        """Cannot archive without permission."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )
        test_db.execute(
            "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (1, 1, 1, 1000, 1000), (2, 1, 2, 1000, 1000)"
        )

        thread = thread_manager.create_thread(1, 1, "Test")

        with pytest.raises(PermissionDeniedError):
            thread_manager.archive_thread(2, thread.id)

    def test_unarchive_thread(self, thread_manager, test_db):
        """Can unarchive thread."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )

        thread = thread_manager.create_thread(1, 1, "Test")
        thread_manager.archive_thread(1, thread.id)

        unarchived = thread_manager.unarchive_thread(1, thread.id)
        assert unarchived.state == ThreadState.ACTIVE

    def test_lock_thread(self, thread_manager, test_db):
        """Can lock thread."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )

        thread = thread_manager.create_thread(1, 1, "Test")

        locked = thread_manager.lock_thread(1, thread.id)
        assert locked.locked

    def test_unlock_thread(self, thread_manager, test_db):
        """Can unlock thread."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )

        thread = thread_manager.create_thread(1, 1, "Test")
        thread_manager.lock_thread(1, thread.id)

        unlocked = thread_manager.unlock_thread(1, thread.id)
        assert not unlocked.locked

    def test_send_message_locked_thread(self, thread_manager, test_db):
        """Cannot send message in locked thread."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )
        test_db.execute(
            "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (1, 1, 1, 1000, 1000), (2, 1, 2, 1000, 1000)"
        )

        thread = thread_manager.create_thread(1, 1, "Test")
        thread_manager.lock_thread(1, thread.id)
        thread_manager.join_thread(2, thread.id)

        with pytest.raises(ThreadLockedError):
            thread_manager.send_message(2, thread.id, "Test")

    def test_get_thread_not_found(self, thread_manager):
        """Get nonexistent thread."""
        thread = thread_manager.get_thread(1, 99999)
        assert thread is None

    def test_get_thread_members(self, thread_manager, test_db):
        """Get thread members."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )
        test_db.execute(
            "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (1, 1, 1, 1000, 1000), (2, 1, 2, 1000, 1000)"
        )

        thread = thread_manager.create_thread(1, 1, "Test")
        thread_manager.join_thread(2, thread.id)

        members = thread_manager.get_thread_members(1, thread.id)
        assert len(members) >= 2

    def test_list_channel_threads(self, thread_manager, test_db):
        """List all threads in channel."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )

        thread_manager.create_thread(1, 1, "Thread 1")
        thread_manager.create_thread(1, 1, "Thread 2")

        threads = thread_manager.get_active_threads(1, 1)
        assert len(threads) >= 2

    def test_auto_archive_inactive_threads(self, thread_manager, test_db, monkeypatch):
        """Inactive threads are auto-archived."""
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
        )

        thread = thread_manager.create_thread(1, 1, "Test")

        # Manually set last_message_at to far in the past to trigger auto-archive
        # ONE_DAY is 1440 minutes
        past_time = thread_manager._get_timestamp() - (1500 * 60 * 1000)
        test_db.execute(
            "UPDATE thread_threads SET last_message_at = ?, created_at = ? WHERE id = ?",
            (past_time, past_time, thread.id),
        )

        # Trigger check via get_active_threads or get_thread
        thread_manager.get_active_threads(1, 1)

        updated = thread_manager.get_thread(1, thread.id)
        assert updated.state == ThreadState.ARCHIVED
