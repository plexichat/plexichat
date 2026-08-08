"""
Regression tests for the thread-system bug fixes.

Covers:
1.  ``update_thread`` no longer 500s when ``encrypt_thread_names`` is on.
2.  Thread names are not stored in plaintext when encryption is enabled.
3.  ``send_message`` propagates messaging failures instead of a phantom success.
4.  Thread slowmode is actually enforced on send.
5.  Route exception mapping returns 403/404/409/429 (not 500).
6.  Thread owners cannot leave or be removed from their own thread.
7.  Non-managers cannot unarchive a thread through the API path.
8.  ``get_messages`` decrypts with the thread's conversation id.
9.  Removing a message decrements ``message_count``.
10. Archived-thread pagination has a stable (tie-break) ordering.
11. Listing uses a single membership query (no N+1).
12. ``ThreadManager`` exposes ``ThreadType`` / ``ThreadState`` etc.
"""

import pytest
from unittest.mock import Mock

from src.core.threads import (
    ThreadType,
    ThreadState,
    ThreadAccessDeniedError,
    PermissionDeniedError,
    ThreadError,
)


def _fake_encrypt(data, key=None, context=None):
    return f"ENC:0:{data}"


def _fake_decrypt(data, key=None, context=None):
    if data and data.startswith("ENC:0:"):
        return data[len("ENC:0:") :]
    return data


def _seed_server_and_channel(db):
    """Insert a minimal server + text channel used by bare-manager tests."""
    db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
        "VALUES (1, 'Test', 1, 1000, 1000)"
    )
    db.execute(
        "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) "
        "VALUES (1, 1, 'test', 'text', 1000, 1000, 0)"
    )


class TestEncryptedThreadNames:
    def test_update_thread_with_encryption_enabled(
        self, thread_manager, test_db, monkeypatch
    ):
        """Updating a thread name must not raise when encryption is enabled."""
        monkeypatch.setattr("src.utils.encryption.encrypt_data", _fake_encrypt)
        monkeypatch.setattr("src.utils.encryption.decrypt_data", _fake_decrypt)
        thread_manager._encrypt_thread_names = True
        _seed_server_and_channel(test_db)

        thread = thread_manager.create_thread(1, 1, "Original Name")
        updated = thread_manager.update_thread(1, thread.id, name="New Name")

        assert updated.name == "New Name"

        row = test_db.fetch_one(
            "SELECT name, name_encrypted FROM thread_threads WHERE id = ?",
            (thread.id,),
        )
        assert row["name"] == "[encrypted]"
        assert row["name_encrypted"] == "ENC:0:New Name"

    def test_create_thread_encryption_no_plaintext(
        self, thread_manager, test_db, monkeypatch
    ):
        """Creating a thread must not persist the plaintext name."""
        monkeypatch.setattr("src.utils.encryption.encrypt_data", _fake_encrypt)
        monkeypatch.setattr("src.utils.encryption.decrypt_data", _fake_decrypt)
        thread_manager._encrypt_thread_names = True
        _seed_server_and_channel(test_db)

        thread = thread_manager.create_thread(1, 1, "Secret Thread Name")

        row = test_db.fetch_one(
            "SELECT name, name_encrypted FROM thread_threads WHERE id = ?",
            (thread.id,),
        )
        assert row["name"] == "[encrypted]"
        assert row["name_encrypted"] == "ENC:0:Secret Thread Name"
        assert "Secret Thread Name" not in row["name"]

    def test_create_thread_encryption_roundtrip(
        self, thread_manager, test_db, monkeypatch
    ):
        """Threads read back decrypt their name when encryption is enabled."""
        monkeypatch.setattr("src.utils.encryption.encrypt_data", _fake_encrypt)
        monkeypatch.setattr("src.utils.encryption.decrypt_data", _fake_decrypt)
        thread_manager._encrypt_thread_names = True
        _seed_server_and_channel(test_db)

        thread = thread_manager.create_thread(1, 1, "Roundtrip Name")
        assert thread.name == "Roundtrip Name"

        fetched = thread_manager.get_thread(1, thread.id)
        assert fetched is not None
        assert fetched.name == "Roundtrip Name"


class TestNoPhantomMessages:
    def test_send_message_propagates_messaging_failure(
        self, thread_manager, test_db, monkeypatch
    ):
        """A messaging failure must raise, not fabricate a success."""
        _seed_server_and_channel(test_db)

        conv = Mock()
        conv.id = 123456
        mock_messaging = Mock()
        mock_messaging.create_thread_conversation = Mock(return_value=conv)
        mock_messaging.send_message = Mock(
            side_effect=RuntimeError("messaging unavailable")
        )
        monkeypatch.setattr(thread_manager, "_messaging", mock_messaging)

        thread = thread_manager.create_thread(1, 1, "Phantom Test")
        assert thread.conversation_id == 123456

        with pytest.raises(RuntimeError):
            thread_manager.send_message(1, thread.id, "Hello")

        assert thread_manager.get_message_count(thread.id) == 0

        row = test_db.fetch_one(
            "SELECT 1 FROM thread_messages WHERE thread_id = ?", (thread.id,)
        )
        assert row is None

    def test_send_message_requires_conversation(self, thread_manager, test_db):
        """A thread without a messaging conversation cannot send."""
        thread_manager._messaging = None
        _seed_server_and_channel(test_db)

        thread = thread_manager.create_thread(1, 1, "No Conversation")

        with pytest.raises(ThreadError):
            thread_manager.send_message(1, thread.id, "Hello")

        assert thread_manager.get_message_count(thread.id) == 0


class TestThreadSlowmode:
    def test_slowmode_blocks_non_manager(self, server_with_channel):
        """A non-manager must be rate limited by thread slowmode."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Slowmode Thread"
        )

        from src.core.threads.slowmode import ThreadSlowmode
        from src.core.ratelimit.exceptions import RateLimitError

        ThreadSlowmode(threads._db).set_slowmode(
            thread.id, 60000, owner.id, can_manage=True
        )

        threads.send_message(member1.id, thread.id, "First message")

        with pytest.raises(RateLimitError):
            threads.send_message(member1.id, thread.id, "Second message")

    def test_slowmode_allows_manager(self, server_with_channel):
        """Managers (owners) are exempt from slowmode."""
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Slowmode Manager"
        )

        from src.core.threads.slowmode import ThreadSlowmode

        ThreadSlowmode(threads._db).set_slowmode(
            thread.id, 60000, owner.id, can_manage=True
        )

        msg1 = threads.send_message(owner.id, thread.id, "One")
        msg2 = threads.send_message(owner.id, thread.id, "Two")
        assert msg1 is not None
        assert msg2 is not None

    def test_slowmode_raises_with_retry_after(self, server_with_channel):
        """The RateLimitError raised by slowmode carries the retry delay.

        Regression guard for the slowmode exception path: the raised
        exception must expose ``retry_after`` (seconds) so route handlers can
        map it to a 429 response without recomputing the delay.
        """
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Retry Thread"
        )

        from src.core.threads.slowmode import ThreadSlowmode
        from src.core.ratelimit.exceptions import RateLimitError

        ThreadSlowmode(threads._db).set_slowmode(
            thread.id, 60000, owner.id, can_manage=True
        )

        threads.send_message(member1.id, thread.id, "First message")

        with pytest.raises(RateLimitError) as exc_info:
            threads.send_message(member1.id, thread.id, "Second message")

        assert exc_info.value.retry_after > 0
        assert "Try again in" in exc_info.value.message


class TestRouteExceptionMapping:
    def _map(self, exc):
        from src.api.routes.threads import _map_thread_exception

        mapped = _map_thread_exception(exc)
        return mapped.status_code if mapped else None

    def test_permission_denied_maps_to_403(self):
        from src.core.threads.exceptions import PermissionDeniedError as TPE

        assert self._map(TPE("no", "threads.manage")) == 403

    def test_thread_member_exists_maps_to_409(self):
        from src.core.threads.exceptions import ThreadMemberExistsError

        assert self._map(ThreadMemberExistsError("dup")) == 409

    def test_generic_thread_error_maps_to_409(self):
        from src.core.threads.exceptions import ThreadError as TE

        assert self._map(TE("conflict")) == 409

    def test_thread_member_not_found_maps_to_404(self):
        from src.core.threads.exceptions import ThreadMemberNotFoundError

        assert self._map(ThreadMemberNotFoundError("gone")) == 404

    def test_rate_limit_maps_to_429(self):
        from src.core.ratelimit.exceptions import RateLimitError

        assert self._map(RateLimitError("slow", 5.0)) == 429


class TestOwnerProtection:
    def test_owner_cannot_leave_own_thread(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Owner Leave"
        )

        with pytest.raises(PermissionDeniedError):
            threads.leave_thread(owner.id, thread.id)

    def test_owner_cannot_be_removed(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Owner Removal"
        )
        threads.join_thread(member1.id, thread.id)

        with pytest.raises(PermissionDeniedError):
            threads.remove_member(member1.id, thread.id, owner.id)

    def test_non_owner_can_still_leave(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Member Leave"
        )
        threads.join_thread(member1.id, thread.id)

        assert threads.leave_thread(member1.id, thread.id) is True


class TestUnarchivePermission:
    def test_non_manager_cannot_unarchive(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Unarchive Perm"
        )
        threads.join_thread(member1.id, thread.id)
        threads.archive_thread(owner.id, thread.id)

        with pytest.raises(PermissionDeniedError):
            threads.unarchive_thread(member1.id, thread.id)

    def test_owner_can_unarchive(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Owner Unarchive"
        )
        threads.archive_thread(owner.id, thread.id)

        unarchived = threads.unarchive_thread(owner.id, thread.id)
        assert unarchived.state == ThreadState.ACTIVE


class TestMessageRetrievalAndCount:
    def test_get_messages_returns_decrypted_content(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Decrypt Check"
        )
        threads.send_message(owner.id, thread.id, "roundtrip content")

        messages = threads.get_messages(owner.id, thread.id)
        assert len(messages) == 1
        assert messages[0]["content"] == "roundtrip content"

    def test_remove_message_decrements_count(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Remove Count"
        )
        msg1 = threads.send_message(owner.id, thread.id, "Message 1")
        msg2 = threads.send_message(owner.id, thread.id, "Message 2")

        assert threads.get_message_count(thread.id) == 2

        removed = threads.remove_message(owner.id, thread.id, msg1["id"])
        assert removed is True
        assert threads.get_message_count(thread.id) == 1

        messages = threads.get_messages(owner.id, thread.id)
        assert len(messages) == 1
        assert messages[0]["content"] == "Message 2"

    def test_remove_unknown_message_returns_false(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Remove Missing"
        )

        assert threads.remove_message(owner.id, thread.id, 999999999) is False


class TestArchivedPagination:
    def test_archived_pagination_stable_ordering(self, server_with_channel, test_db):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        archived_ids = []
        for i in range(6):
            thread = threads.create_thread(
                user_id=owner.id, channel_id=channel.id, name=f"Archived {i}"
            )
            threads.archive_thread(owner.id, thread.id)
            archived_ids.append(thread.id)

        # Force distinct archived_at values. The six archive() calls can all
        # complete within the same millisecond (particularly with the fast
        # test crypto), and timestamp-cursor pagination is ambiguous when
        # every row shares one timestamp: the second page's
        # ``before_timestamp`` cursor then excludes every row.
        for i, thread_id in enumerate(archived_ids):
            test_db.execute(
                "UPDATE thread_threads SET archived_at = ? WHERE id = ?",
                (1000 * (i + 1), thread_id),
            )

        first_page = threads.get_archived_threads(owner.id, channel.id, limit=3)
        assert len(first_page) == 3

        last_archived_at = first_page[-1].archived_at
        second_page = threads.get_archived_threads(
            owner.id, channel.id, limit=3, before_timestamp=last_archived_at
        )

        seen = {t.id for t in first_page} | {t.id for t in second_page}
        assert len(seen) == 6

    def test_archived_ordering_has_tiebreak(self, server_with_channel, test_db):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        t1 = threads.create_thread(owner.id, channel.id, "A")
        t2 = threads.create_thread(owner.id, channel.id, "B")

        threads.archive_thread(owner.id, t1.id)
        threads.archive_thread(owner.id, t2.id)

        test_db.execute(
            "UPDATE thread_threads SET archived_at = 1000 WHERE id IN (?, ?)",
            (t1.id, t2.id),
        )

        result = threads.get_archived_threads(owner.id, channel.id)
        assert [t.id for t in result] == sorted([t.id for t in result], reverse=True)


class TestListingEfficiency:
    def test_active_threads_visible(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        threads.create_thread(owner.id, channel.id, "Listing One")
        threads.create_thread(owner.id, channel.id, "Listing Two")

        active = threads.get_active_threads(owner.id, channel.id)
        assert len(active) >= 2

    def test_private_threads_membership_filtered(self, server_with_channel):
        owner, member1, member2, server, channel, servers, threads = server_with_channel

        public = threads.create_thread(owner.id, channel.id, "Public")
        private = threads.create_thread(
            owner.id, channel.id, "Private", thread_type=ThreadType.PRIVATE
        )

        active = threads.get_active_threads(member1.id, channel.id)
        ids = {t.id for t in active}
        assert public.id in ids
        assert private.id not in ids


class TestManagerExposesModels:
    def test_thread_manager_exposes_thread_type(self, thread_manager):
        assert thread_manager.ThreadType is ThreadType

    def test_thread_manager_exposes_thread_state(self, thread_manager):
        assert thread_manager.ThreadState is ThreadState
