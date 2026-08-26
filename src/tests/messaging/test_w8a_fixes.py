"""Regression tests for W8-A messaging fixes (findings Agent 13 / Agent 57 F4).

Covers:
- M3: optimistic-locking token on edits (expected_edited_at -> conflict)
- Parity#1: configurable messaging.edit_window_seconds edit window
- Parity#2: record_edit wiring + edited_at column population
- L2: send_system_message routes through the encryption path
- L3: forwarding decrypts-then-re-encrypts, stores NO original_content,
      forward-history reads are participant-only
- L4: attachment URL scheme allowlist at send time
"""

import json

import pytest

from src.core.messaging.exceptions import (
    MessageConflictError,
    MessageEditWindowExpiredError,
)


def _raw_row(db, message_id):
    return db.fetch_one("SELECT * FROM msg_messages WHERE id = ?", (message_id,))


class TestOptimisticLocking:
    def test_stale_token_raises_conflict(self, messaging_manager, two_users):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "hello")

        revision = msg.updated_at
        messaging_manager.edit_message(
            user1.id, msg.id, "first", expected_edited_at=revision
        )

        with pytest.raises(MessageConflictError):
            messaging_manager.edit_message(
                user1.id, msg.id, "second", expected_edited_at=revision
            )

    def test_fresh_token_succeeds(self, messaging_manager, two_users):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "hello")

        edited = messaging_manager.edit_message(
            user1.id, msg.id, "v2", expected_edited_at=msg.updated_at
        )
        # The response carries the real new revision so clients can chain.
        assert edited.edited_at is not None

    def test_omitted_token_stays_backward_compatible(
        self, messaging_manager, two_users
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "hello")
        messaging_manager.edit_message(user1.id, msg.id, "a")
        messaging_manager.edit_message(user1.id, msg.id, "b")


class TestEditWindow:
    @pytest.fixture
    def window_manager(self, db):
        from src.core.messaging.manager import MessagingManager

        manager = MessagingManager(db)
        svc_config = manager._message_svc._config
        svc_config["edit_window_seconds"] = 60
        yield manager
        svc_config.pop("edit_window_seconds", None)

    def _aged_message(self, db, messaging_manager, two_users, age_ms):
        user1, _ = two_users
        dm = messaging_manager.create_dm(user1.id, two_users[1].id)
        msg = messaging_manager.send_message(user1.id, dm.id, "old")
        db.execute(
            "UPDATE msg_messages SET created_at = ? WHERE id = ?",
            (msg.created_at - age_ms, msg.id),
        )
        return dm, msg

    def test_expired_window_rejected(self, db, window_manager, two_users):
        _, msg = self._aged_message(db, window_manager, two_users, 61 * 1000)
        with pytest.raises(MessageEditWindowExpiredError):
            window_manager.edit_message(two_users[0].id, msg.id, "late")

    def test_within_window_allowed(self, db, window_manager, two_users):
        _, msg = self._aged_message(db, window_manager, two_users, 10 * 1000)
        edited = window_manager.edit_message(two_users[0].id, msg.id, "fresh")
        assert edited.content == "fresh"

    def test_no_window_by_default(self, db, messaging_manager, two_users):
        _, msg = self._aged_message(
            db, messaging_manager, two_users, 365 * 24 * 3600 * 1000
        )
        edited = messaging_manager.edit_message(two_users[0].id, msg.id, "ancient")
        assert edited.content == "ancient"


class TestEditedAtAndHistory:
    def test_edited_at_populated_on_edit(self, db, messaging_manager, two_users):
        user1, _ = two_users
        dm = messaging_manager.create_dm(user1.id, two_users[1].id)
        msg = messaging_manager.send_message(user1.id, dm.id, "v1")

        before_row = _raw_row(db, msg.id)
        assert before_row["edited_at"] is None  # never edited yet

        messaging_manager.edit_message(user1.id, msg.id, "v2")

        row = _raw_row(db, msg.id)
        assert row["edited"] == 1
        assert row["edited_at"] is not None
        assert int(row["edited_at"]) >= int(before_row["updated_at"]) - 1000

    def test_record_edit_wired_into_edit_path(self, db, messaging_manager, two_users):
        user1, _ = two_users
        dm = messaging_manager.create_dm(user1.id, two_users[1].id)
        msg = messaging_manager.send_message(user1.id, dm.id, "original text")
        messaging_manager.edit_message(user1.id, msg.id, "edited text")

        rows = db.fetch_all(
            "SELECT old_content, old_content_encrypted FROM msg_edit_history "
            "WHERE message_id = ?",
            (msg.id,),
        )
        assert rows, "edit history must be recorded on every real edit"

    def test_prior_plaintext_not_stored_alongside_blob(
        self, db, messaging_manager, two_users
    ):
        user1, _ = two_users
        dm = messaging_manager.create_dm(user1.id, two_users[1].id)
        msg = messaging_manager.send_message(user1.id, dm.id, "secret prior")
        messaging_manager.edit_message(user1.id, msg.id, "new")

        rows = db.fetch_all(
            "SELECT old_content, old_content_encrypted FROM msg_edit_history "
            "WHERE message_id = ?",
            (msg.id,),
        )
        assert rows
        for r in rows:
            if r["old_content_encrypted"]:
                assert r["old_content"] != "secret prior"
                assert r["old_content"] == "[encrypted]"


class TestSystemMessageEncryption:
    def test_system_message_stored_encrypted_and_flagged(
        self, db, messaging_manager, two_users
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        sys_msg = messaging_manager.send_system_message(
            dm.id, "System announcement", "announcement"
        )
        # Round-trips to plaintext through the same decryption path as users.
        assert sys_msg.content == "System announcement"

        raw = _raw_row(db, sys_msg.id)
        assert raw["content"].startswith("ENC:")
        metadata = json.loads(raw["metadata"])
        assert metadata.get("system") is True
        assert metadata.get("event_type") == "announcement"


class TestAttachmentUrlAllowlist:
    @pytest.fixture
    def service(self, db):
        from src.core.messaging.services.content_filter import ContentFilterService
        from src.core.messaging.services.message.composer import MessageService
        from src.core.messaging.services.participant import ParticipantService
        from src.core.messaging.services.user_settings import UserSettingsService

        return MessageService(
            db,
            ParticipantService(db),
            UserSettingsService(db),
            ContentFilterService(db),
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://cdn.example.com/file.png",
            "http://cdn.example.com/file.png",
            "/api/v1/media/attachments/abc",
            "/uploads/x/y.png",
        ],
    )
    def test_allowed(self, service, url):
        assert service._normalize_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html;base64,AAAA",
            "file:///etc/passwd",
            "//evil.example.com/x.png",
            "vbscript:x",
        ],
    )
    def test_rejected(self, service, url):
        from src.core.messaging.exceptions import InvalidContentError

        with pytest.raises(InvalidContentError):
            service._normalize_url(url)


class TestForwardingEncrypted:
    def test_forward_decrypts_and_reencrypts_for_destination(
        self, db, messaging_manager, two_users
    ):
        from src.core.messaging.services.forwarding import ForwardingService

        user1, user2 = two_users
        source_dm = messaging_manager.create_dm(user1.id, user2.id)
        target_dm = messaging_manager.create_dm(user2.id, user1.id)
        original = messaging_manager.send_message(user1.id, source_dm.id, "forward me")

        svc = ForwardingService(
            db,
            messaging_manager,
            getattr(messaging_manager, "_participant_svc", None),
        )
        result = svc.forward_message(user1.id, original.id, target_dm.id)

        raw = _raw_row(db, result["new_message_id"])
        # Destination storage is re-encrypted for the destination — never
        # literal source ciphertext.
        assert raw["content"].startswith("ENC:")
        assert raw["author_id"] == user1.id

        # No ciphertext/plaintext duplication into msg_forwarded.
        fwd = db.fetch_one(
            "SELECT original_content FROM msg_forwarded WHERE id = ?",
            (result["forward_id"],),
        )
        assert fwd is not None
        assert fwd["original_content"] is None

    def test_forward_history_denies_non_participant(
        self, db, messaging_manager, modules
    ):
        import uuid

        from src.core.messaging.services.forwarding import ForwardingService

        unique = uuid.uuid4().hex[:8]
        author = modules.auth.register(
            username=f"fwd_a_{unique}",
            email=f"fwd_a_{unique}@example.com",
            password="TestPass123!",
        )
        recipient = modules.auth.register(
            username=f"fwd_b_{unique}",
            email=f"fwd_b_{unique}@example.com",
            password="TestPass123!",
        )
        outsider = modules.auth.register(
            username=f"fwd_o_{unique}",
            email=f"fwd_o_{unique}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(author.id, recipient.id)
        msg = messaging_manager.send_message(author.id, dm.id, "private")

        svc = ForwardingService(
            db,
            messaging_manager,
            getattr(messaging_manager, "_participant_svc", None),
        )
        with pytest.raises(PermissionError):
            svc.get_forward_history(msg.id, requester_id=outsider.id)

    def test_forward_history_allows_participant(self, db, messaging_manager, two_users):
        from src.core.messaging.services.forwarding import ForwardingService

        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "x")

        svc = ForwardingService(
            db,
            messaging_manager,
            getattr(messaging_manager, "_participant_svc", None),
        )
        history = svc.get_forward_history(msg.id, requester_id=user2.id)
        assert history == []
