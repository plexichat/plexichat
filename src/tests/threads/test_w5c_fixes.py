"""
Regression tests for FIXER W5-C Agent 32 fixes.

Covers:
1. [H1] Explicit unarchive is not instantly undone by auto-archive.
2. [M1] ``remove_message`` requires author-or-thread-manager.
3. [M2] User thread listings re-check channel access.
4. [M3] ``add_member`` rejects users outside the backing server.
5. [M4] Permission checks fail closed when the servers module is absent.
6. [M6] Thread message payloads never include internal/ciphertext columns.
7. [H3] Deleting a message unlinks thread references and starter linkage.
8. [L2] ``create_thread_from_message`` validates the URL channel matches.
"""

import pytest

from src.core.threads import ChannelNotFoundError, ThreadAccessDeniedError
from src.core.threads.manager import ThreadManager


def _backdate_thread(test_db, thread_id, minutes):
    row = test_db.fetch_one(
        "SELECT created_at FROM thread_threads WHERE id = ?", (thread_id,)
    )
    base = row["created_at"]
    test_db.execute(
        "UPDATE thread_threads SET created_at = ?, last_message_at = ? WHERE id = ?",
        (base - minutes * 60 * 1000, base - minutes * 60 * 1000, thread_id),
    )


class TestUnarchiveNotRearchived:
    def test_unarchive_suppresses_auto_archive(self, server_with_channel, test_db):
        """[H1] A stale archived thread stays ACTIVE after explicit unarchive."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Stale Unarchive"
        )
        # Make the thread older than its auto-archive window.
        _backdate_thread(test_db, thread.id, 1500)

        threads.archive_thread(owner.id, thread.id)
        unarchived = threads.unarchive_thread(owner.id, thread.id)
        assert unarchived.state == threads.ThreadState.ACTIVE

        # Previously get_thread immediately re-ran auto-archive here.
        refetched = threads.get_thread(owner.id, thread.id)
        assert refetched.state == threads.ThreadState.ACTIVE


class TestRemoveMessageAuthorization:
    def test_non_manager_cannot_remove_others_message(self, server_with_channel):
        """[M1] Only the sender or a thread manager may remove a link."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Remove Auth"
        )
        threads.join_thread(member1.id, thread.id)
        msg = threads.send_message(member1.id, thread.id, "mine")

        with pytest.raises(ThreadAccessDeniedError):
            threads.remove_message(member2.id, thread.id, msg["id"])

    def test_manager_can_remove_others_message(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Remove Mod"
        )
        threads.join_thread(member1.id, thread.id)
        msg = threads.send_message(member1.id, thread.id, "theirs")

        assert threads.remove_message(owner.id, thread.id, msg["id"]) is True
        assert threads.get_message_count(thread.id) == 0


class TestUserListingAccessRecheck:
    def test_user_threads_filtered_after_access_loss(
        self, server_with_channel, monkeypatch
    ):
        """[M2] Threads whose channel view was revoked are not listed."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        hidden = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Hidden After Revoke"
        )
        visible = threads.create_thread(
            user_id=owner.id,
            channel_id=channel.id,
            name="Still Visible",
            thread_type=threads.ThreadType.PRIVATE,
        )
        threads.add_member(owner.id, hidden.id, member1.id)
        threads.add_member(owner.id, visible.id, member1.id)

        # Simulate loss of server/channel visibility for member1's public
        # threads; private-thread visibility still depends on membership.
        monkeypatch.setattr(threads, "_check_permission", lambda *a, **k: False)

        names = {t.name for t in threads.get_user_threads(member1.id)}
        assert "Hidden After Revoke" not in names
        assert "Still Visible" in names


class TestAddMemberValidation:
    def test_add_member_rejects_non_server_member(self, server_with_channel, user_pool):
        """[M3] Existing users outside the backing server cannot be added."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        outsider = user_pool.get_user()
        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Public Add"
        )

        with pytest.raises(ThreadAccessDeniedError):
            threads.add_member(owner.id, thread.id, outsider.id)

    def test_add_member_rejects_unknown_user(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Unknown Target"
        )

        from src.core.threads import ThreadMemberNotFoundError

        with pytest.raises(ThreadMemberNotFoundError):
            threads.add_member(owner.id, thread.id, 999999999)


class TestFailClosedPermissions:
    def test_no_servers_module_denies_public_thread_access(self, server_with_channel):
        """[M4] Without a servers module, public-thread checks deny."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Fail Closed"
        )

        bare = ThreadManager(threads._db)
        assert bare.can_view_thread(owner.id, thread.id) is False
        assert bare.can_send_in_thread(owner.id, thread.id) is False
        assert bare.get_active_threads(owner.id, channel.id) == []


class TestMessagePayloadProjection:
    def test_get_messages_excludes_internal_columns(self, server_with_channel):
        """[M6] No ciphertext/internal storage fields in payloads."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Projection"
        )
        threads.send_message(owner.id, thread.id, "hello")

        msgs = threads.get_messages(owner.id, thread.id)
        assert len(msgs) == 1
        forbidden = {"content_encrypted", "message_created_at"}
        assert forbidden.isdisjoint(msgs[0].keys())
        assert msgs[0]["content"] == "hello"


class TestDeletedMessageUnlinking:
    def test_unlink_removes_reference_and_count(self, server_with_channel):
        """[H3] unlink_deleted_message drops links and keeps counts honest."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Unlink"
        )
        msg1 = threads.send_message(owner.id, thread.id, "One")
        threads.send_message(owner.id, thread.id, "Two")

        removed = threads.unlink_deleted_message(int(msg1["id"]))
        assert removed == 1
        assert threads.get_message_count(thread.id) == 1
        contents = [m["content"] for m in threads.get_messages(owner.id, thread.id)]
        assert contents == ["Two"]

    def test_unlink_clears_starter_linkage(self, server_with_channel, test_db):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        root = servers.send_channel_message(owner.id, channel.id, "root message")
        thread = threads.create_thread_from_message(owner.id, root.id, "Starter Thread")
        assert thread.parent_message_id == root.id

        threads.unlink_deleted_message(int(root.id))

        row = test_db.fetch_one(
            "SELECT parent_message_id FROM thread_threads WHERE id = ?",
            (thread.id,),
        )
        assert row["parent_message_id"] is None


class TestCreateFromMessageChannelMatch:
    def test_mismatched_url_channel_rejected(self, server_with_channel):
        """[L2] Parent message conversation must match the URL channel."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel
        other = servers.create_channel(
            owner.id, server.id, "other", channel_type=servers.ChannelType.TEXT
        )

        root = servers.send_channel_message(owner.id, channel.id, "root")
        with pytest.raises(ChannelNotFoundError):
            threads.create_thread_from_message(
                owner.id, root.id, "Mismatched", expected_channel_id=int(other.id)
            )


class TestDeleteThreadsForChannelCascade:
    def test_cascade_is_atomic_and_clears_starter_linkage(
        self, server_with_channel, test_db
    ):
        """W5 cascade integrity: one transaction deletes links + members,
        marks threads deleted, and NULLs parent_message_id."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        root = servers.send_channel_message(owner.id, channel.id, "root")
        thread = threads.create_thread_from_message(owner.id, root.id, "Cascade")
        threads.join_thread(member1.id, thread.id)
        threads.send_message(member1.id, thread.id, "hello")

        deleted_ids = threads.delete_threads_for_channel(channel.id)
        assert deleted_ids == [thread.id]

        row = test_db.fetch_one(
            "SELECT deleted, parent_message_id FROM thread_threads WHERE id = ?",
            (thread.id,),
        )
        assert row["deleted"] == 1
        assert row["parent_message_id"] is None
        assert (
            test_db.fetch_one(
                "SELECT COUNT(*) AS c FROM thread_messages WHERE thread_id = ?",
                (thread.id,),
            )["c"]
            == 0
        )
        assert (
            test_db.fetch_one(
                "SELECT COUNT(*) AS c FROM thread_members WHERE thread_id = ?",
                (thread.id,),
            )["c"]
            == 0
        )

    def test_rollback_on_failure_leaves_threads_intact(
        self, server_with_channel, test_db
    ):
        """A mid-cascade failure rolls back the WHOLE cascade."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel
        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Rollback"
        )
        manager = ThreadManager.__new__(ThreadManager)

        class ExplodingDB:
            def __getattr__(self, name):
                if name == "fetch_all":
                    return test_db.fetch_all
                raise AttributeError(name)

            def fetch_all(self, sql, params=()):
                return test_db.fetch_all(sql, params)

            def begin_transaction(self):
                test_db.begin_transaction()

            def execute(self, sql, params=()):
                raise RuntimeError("boom")

            def rollback(self):
                test_db.rollback()

        manager._db = ExplodingDB()

        from src.core.base import timestamp_ms

        manager._get_timestamp = timestamp_ms

        with pytest.raises(RuntimeError):
            manager.delete_threads_for_channel(channel.id)

        # Rolled back: the thread survived untouched.
        row = test_db.fetch_one(
            "SELECT deleted FROM thread_threads WHERE id = ?", (thread.id,)
        )
        assert row["deleted"] == 0
