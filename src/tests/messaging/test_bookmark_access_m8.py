"""Regression tests for M8: bookmark without access check (ID-probing oracle).

Pre-fix behaviour: ``POST /api/v1/features/bookmarks`` inserted a bookmark
for an arbitrary ``message_id`` (201) or failed with status codes that
distinguished "message exists but no access" (403) from "no such message"
(400) — letting an attacker probe private message IDs for existence.

Post-fix contract (mirrors the message-GET route, which returns 404 for
non-viewable messages rather than 403):
- viewable message + participant -> success (HTTP 200 ``{"success": True}``)
- no-access / conversation-mismatch / nonexistent message -> IDENTICAL
  HTTP 404 ``{"error": {"code": 404, "message": "Message not found"}}``
- conversation-scoped listing without participation -> 404
  (``"Conversation not found"``), never 403/400
- listing silently drops bookmarks whose message is no longer viewable
- no ``str(e)`` / ``message=str(e)`` leakage in the bookmark routes
"""

import inspect
import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption
from src.core.messaging.services.bookmarks import BookmarkService

BOOKMARK_URL = "/api/v1/features/bookmarks"


def _register_and_login(auth_manager, label="m8_user"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return user, result.token


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def trio(auth_manager):
    users = {}
    for label in ("alice", "bob", "mallory"):
        user, token = _register_and_login(auth_manager, label)
        users[label] = (user, token)
    return users


def _dm_with_message(messaging_manager, a, b, content="secret hello"):
    dm = messaging_manager.create_dm(a.id, b.id)
    msg = messaging_manager.send_message(a.id, dm.id, content)
    return dm, msg


# ---------------------------------------------------------------------------
# Service layer: access is enforced before INSERT
# ---------------------------------------------------------------------------


class TestServiceAccess:
    def test_participant_can_bookmark(self, db, messaging_manager, trio):
        alice, _ = trio["alice"]
        bob, _ = trio["bob"]
        dm, msg = _dm_with_message(messaging_manager, alice, bob)
        svc = BookmarkService(db, messaging_manager)
        record = svc.add_bookmark(bob.id, msg.id, dm.id, label="mine")
        assert record["message_id"] == msg.id
        assert record["conversation_id"] == dm.id

    def test_outsider_bookmark_raises(self, db, messaging_manager, trio):
        alice, _ = trio["alice"]
        bob, _ = trio["bob"]
        mallory, _ = trio["mallory"]
        dm, msg = _dm_with_message(messaging_manager, alice, bob)
        svc = BookmarkService(db, messaging_manager)
        with pytest.raises((PermissionError, ValueError)):
            svc.add_bookmark(mallory.id, msg.id, dm.id)

    def test_nonexistent_message_raises(self, db, messaging_manager, trio):
        alice, _ = trio["alice"]
        bob, _ = trio["bob"]
        dm, _msg = _dm_with_message(messaging_manager, alice, bob)
        svc = BookmarkService(db, messaging_manager)
        with pytest.raises((PermissionError, ValueError)):
            svc.add_bookmark(bob.id, 999999999, dm.id)

    def test_conversation_mismatch_raises(self, db, messaging_manager, trio):
        alice, _ = trio["alice"]
        bob, _ = trio["bob"]
        mallory, _ = trio["mallory"]
        dm, msg = _dm_with_message(messaging_manager, alice, bob)
        other = messaging_manager.create_dm(alice.id, mallory.id)
        svc = BookmarkService(db, messaging_manager)
        with pytest.raises((PermissionError, ValueError)):
            svc.add_bookmark(bob.id, msg.id, other.id)

    def test_list_keeps_viewable(self, db, messaging_manager, trio):
        alice, _ = trio["alice"]
        bob, _ = trio["bob"]
        dm, msg = _dm_with_message(messaging_manager, alice, bob)
        svc = BookmarkService(db, messaging_manager)
        svc.add_bookmark(bob.id, msg.id, dm.id)
        rows = svc.get_bookmarks(bob.id)
        assert len(rows) == 1
        assert rows[0]["message_id"] == msg.id
        assert "content_preview" in rows[0]

    def test_list_drops_no_longer_viewable(self, db):
        """Rows whose message fails the retrieval-path view gate are dropped.

        Uses a stub messaging module so the assertion targets the filtering
        logic itself (no participant/message caches involved): ``get_message``
        returning None — exactly what the real retrieval path yields for a
        non-participant or a missing row — must hide the bookmark metadata.
        """
        from unittest.mock import MagicMock

        messaging = MagicMock()
        messaging.get_message.return_value = None
        svc = BookmarkService(db, messaging)
        db.execute(
            """INSERT INTO user_bookmarks
               (id, user_id, message_id, conversation_id, label, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (424201, 424202, 424203, 424204, "stale", 1700000000000),
        )
        assert svc.get_bookmarks(424202) == []

        # ... while a viewable message keeps its bookmark with a preview.
        from types import SimpleNamespace

        messaging.get_message.return_value = SimpleNamespace(
            content="hello", author_id=424205, created_at=1700000000001
        )
        rows = svc.get_bookmarks(424202)
        assert len(rows) == 1
        assert rows[0]["content_preview"] == "hello"

    def test_list_conversation_filter_requires_participation(
        self, db, messaging_manager, trio
    ):
        alice, _ = trio["alice"]
        bob, _ = trio["bob"]
        mallory, _ = trio["mallory"]
        dm, _msg = _dm_with_message(messaging_manager, alice, bob)
        svc = BookmarkService(db, messaging_manager)
        with pytest.raises((PermissionError, ValueError)):
            svc.get_bookmarks(mallory.id, dm.id)


# ---------------------------------------------------------------------------
# HTTP layer: identical 404s, no existence oracle
# ---------------------------------------------------------------------------


class TestRouteOracle:
    def test_viewable_bookmark_succeeds(self, test_client, messaging_manager, trio):
        alice, _ = trio["alice"]
        bob, bob_tok = trio["bob"]
        dm, msg = _dm_with_message(messaging_manager, alice, bob)
        resp = test_client.post(
            BOOKMARK_URL,
            json={"message_id": str(msg.id), "conversation_id": str(dm.id)},
            headers=_headers(bob_tok),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_no_access_returns_404(self, test_client, messaging_manager, trio):
        alice, _ = trio["alice"]
        bob, _ = trio["bob"]
        mallory, mallory_tok = trio["mallory"]
        dm, msg = _dm_with_message(messaging_manager, alice, bob)
        resp = test_client.post(
            BOOKMARK_URL,
            json={"message_id": str(msg.id), "conversation_id": str(dm.id)},
            headers=_headers(mallory_tok),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json() == {"error": {"code": 404, "message": "Message not found"}}

    def test_probing_returns_identical_status(
        self, test_client, messaging_manager, trio
    ):
        """Nonexistent vs no-access IDs are indistinguishable (no oracle)."""
        alice, _ = trio["alice"]
        bob, bob_tok = trio["bob"]
        mallory, mallory_tok = trio["mallory"]
        dm, msg = _dm_with_message(messaging_manager, alice, bob)

        no_access = test_client.post(
            BOOKMARK_URL,
            json={"message_id": str(msg.id), "conversation_id": str(dm.id)},
            headers=_headers(mallory_tok),
        )
        nonexistent = test_client.post(
            BOOKMARK_URL,
            json={"message_id": "999999999", "conversation_id": str(dm.id)},
            headers=_headers(bob_tok),
        )
        mismatch = test_client.post(
            BOOKMARK_URL,
            json={"message_id": str(msg.id), "conversation_id": "999999998"},
            headers=_headers(bob_tok),
        )
        assert no_access.status_code == 404, no_access.text
        assert nonexistent.status_code == 404, nonexistent.text
        assert mismatch.status_code == 404, mismatch.text
        assert no_access.json() == nonexistent.json() == mismatch.json()

    def test_list_conversation_filter_no_access_is_404(
        self, test_client, messaging_manager, trio
    ):
        alice, _ = trio["alice"]
        bob, _ = trio["bob"]
        mallory, mallory_tok = trio["mallory"]
        dm, _msg = _dm_with_message(messaging_manager, alice, bob)
        resp = test_client.get(
            BOOKMARK_URL,
            params={"conversation_id": str(dm.id)},
            headers=_headers(mallory_tok),
        )
        assert resp.status_code == 404, resp.text

    def test_no_exception_text_leak(self):
        from src.api.routes.features import bookmarks as bookmarks_route

        source = inspect.getsource(bookmarks_route)
        assert "str(e)" not in source
        assert "message=str(" not in source


# ---------------------------------------------------------------------------
# Server-channel targets: the client only holds the channel id
# ---------------------------------------------------------------------------


class TestChannelTargetBookmark:
    """A channel message is addressed by its channel id, stored by conversation id.

    Regression: ``_ensure_bookmark_access`` compared the two literally, so
    bookmarking any server-channel message answered 404 while the same message
    was fully readable in the channel.
    """

    def test_channel_id_bookmarks_channel_message(
        self, test_client, server_manager, trio
    ):
        alice, alice_tok = trio["alice"]
        unique = uuid.uuid4().hex[:6]
        server = server_manager.create_server(owner_id=alice.id, name=f"bm-{unique}")
        channel = server_manager.create_channel(alice.id, server.id, f"bm-{unique}")
        conv_id = server_manager.get_channel_conversation_id(channel.id)
        assert int(conv_id) != int(channel.id), "fixture must model distinct ids"
        msg = server_manager.send_channel_message(alice.id, channel.id, "bookmark me")

        resp = test_client.post(
            BOOKMARK_URL,
            json={"message_id": str(msg.id), "conversation_id": str(channel.id)},
            headers=_headers(alice_tok),
        )
        assert resp.status_code == 200, resp.text
        # Stored under the client-facing channel id so the UI can jump back.
        assert int(resp.json()["bookmark"]["conversation_id"]) == int(channel.id)

    def test_other_channel_id_still_404(self, test_client, server_manager, trio):
        alice, alice_tok = trio["alice"]
        unique = uuid.uuid4().hex[:6]
        server = server_manager.create_server(owner_id=alice.id, name=f"bm2-{unique}")
        channel = server_manager.create_channel(alice.id, server.id, f"one-{unique}")
        other = server_manager.create_channel(alice.id, server.id, f"two-{unique}")
        msg = server_manager.send_channel_message(alice.id, channel.id, "secret")

        resp = test_client.post(
            BOOKMARK_URL,
            json={"message_id": str(msg.id), "conversation_id": str(other.id)},
            headers=_headers(alice_tok),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json() == {"error": {"code": 404, "message": "Message not found"}}

    def test_non_member_channel_id_still_404(self, test_client, server_manager, trio):
        alice, _ = trio["alice"]
        _mallory, mallory_tok = trio["mallory"]
        unique = uuid.uuid4().hex[:6]
        server = server_manager.create_server(owner_id=alice.id, name=f"bm3-{unique}")
        channel = server_manager.create_channel(alice.id, server.id, f"priv-{unique}")
        msg = server_manager.send_channel_message(alice.id, channel.id, "members only")

        resp = test_client.post(
            BOOKMARK_URL,
            json={"message_id": str(msg.id), "conversation_id": str(channel.id)},
            headers=_headers(mallory_tok),
        )
        assert resp.status_code == 404, resp.text
