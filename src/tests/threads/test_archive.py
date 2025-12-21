"""
Tests for thread archive functionality.
"""

import pytest
from src.core.threads import (
    ThreadState,
    AutoArchiveDuration,
    ThreadNotFoundError,
    PermissionDeniedError,
)


class TestArchiveThread:
    """Tests for archiving threads."""

    def test_archive_thread(self, server_with_channel):
        """Test archiving a thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Archive Test"
        )

        archived = threads.archive_thread(owner.id, thread.id)

        assert archived.state == ThreadState.ARCHIVED
        assert archived.archived_at is not None

    def test_archive_thread_by_owner(self, server_with_channel):
        """Test that owner can archive their thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=member1.id,
            channel_id=channel.id,
            name="Owner Archive Test"
        )

        archived = threads.archive_thread(member1.id, thread.id)
        assert archived.state == ThreadState.ARCHIVED

    def test_archive_already_archived_thread(self, server_with_channel):
        """Test archiving already archived thread returns same state."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Already Archived Test"
        )

        threads.archive_thread(owner.id, thread.id)
        archived = threads.archive_thread(owner.id, thread.id)

        assert archived.state == ThreadState.ARCHIVED

    def test_archive_nonexistent_thread_fails(self, server_with_channel):
        """Test archiving nonexistent thread fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.archive_thread(owner.id, 999999999)

    def test_archive_without_permission_fails(self, server_with_channel):
        """Test that non-owner without permission cannot archive."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Permission Archive Test"
        )

        threads.join_thread(member1.id, thread.id)

        with pytest.raises(PermissionDeniedError):
            threads.archive_thread(member1.id, thread.id)


class TestUnarchiveThread:
    """Tests for unarchiving threads."""

    def test_unarchive_thread(self, server_with_channel):
        """Test unarchiving a thread."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Unarchive Test"
        )

        threads.archive_thread(owner.id, thread.id)
        unarchived = threads.unarchive_thread(owner.id, thread.id)

        assert unarchived.state == ThreadState.ACTIVE
        assert unarchived.archived_at is None

    def test_unarchive_active_thread(self, server_with_channel):
        """Test unarchiving active thread returns same state."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Active Unarchive Test"
        )

        unarchived = threads.unarchive_thread(owner.id, thread.id)
        assert unarchived.state == ThreadState.ACTIVE

    def test_unarchive_nonexistent_thread_fails(self, server_with_channel):
        """Test unarchiving nonexistent thread fails."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        with pytest.raises(ThreadNotFoundError):
            threads.unarchive_thread(owner.id, 999999999)

    def test_member_can_unarchive_by_sending_message(self, server_with_channel):
        """Test that member can unarchive by sending message."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Member Unarchive Test"
        )

        threads.join_thread(member1.id, thread.id)
        threads.archive_thread(owner.id, thread.id)

        threads.send_message(member1.id, thread.id, "Unarchive!")

        updated = threads.get_thread(owner.id, thread.id)
        assert updated.state == ThreadState.ACTIVE


class TestAutoArchive:
    """Tests for auto-archive functionality."""

    def test_auto_archive_duration_one_hour(self, server_with_channel):
        """Test thread with one hour auto-archive duration."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="One Hour Archive Test",
            auto_archive_duration=AutoArchiveDuration.ONE_HOUR
        )

        assert thread.auto_archive_duration == AutoArchiveDuration.ONE_HOUR

    def test_auto_archive_duration_one_day(self, server_with_channel):
        """Test thread with one day auto-archive duration."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="One Day Archive Test",
            auto_archive_duration=AutoArchiveDuration.ONE_DAY
        )

        assert thread.auto_archive_duration == AutoArchiveDuration.ONE_DAY

    def test_auto_archive_duration_three_days(self, server_with_channel):
        """Test thread with three days auto-archive duration."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Three Days Archive Test",
            auto_archive_duration=AutoArchiveDuration.THREE_DAYS
        )

        assert thread.auto_archive_duration == AutoArchiveDuration.THREE_DAYS

    def test_auto_archive_duration_seven_days(self, server_with_channel):
        """Test thread with seven days auto-archive duration."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Seven Days Archive Test",
            auto_archive_duration=AutoArchiveDuration.SEVEN_DAYS
        )

        assert thread.auto_archive_duration == AutoArchiveDuration.SEVEN_DAYS

    def test_update_auto_archive_duration(self, server_with_channel):
        """Test updating auto-archive duration."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Update Duration Test",
            auto_archive_duration=AutoArchiveDuration.ONE_HOUR
        )

        updated = threads.update_thread(
            owner.id,
            thread.id,
            auto_archive_duration=AutoArchiveDuration.SEVEN_DAYS
        )

        assert updated.auto_archive_duration == AutoArchiveDuration.SEVEN_DAYS
