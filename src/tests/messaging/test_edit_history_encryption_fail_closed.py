"""Regression tests: edit history must never persist plaintext when
encryption-at-rest is enabled, even if the encryption operation fails.

``EditHistoryService.record_edit`` used to catch a failed ``encrypt_data``
call and then store ``old_content`` verbatim. That is a fail-open on the
encryption-at-rest contract: exactly when the keyring is misconfigured, prior
message content (potentially sensitive) was written to the database in the
clear. The fix substitutes a non-reversible redaction marker instead of the
plaintext, keeping the history row without leaking its content.

Run with: pytest src/tests/messaging/test_edit_history_encryption_fail_closed.py -q
"""

import pytest

import src.core.messaging.services.edit_history as edit_history_service
from src.core.messaging.services.edit_history import EditHistoryService


class _AllowAllParticipants:
    """Participant check stub: every user may access every conversation."""

    def is_participant(self, conversation_id, user_id):
        return True


def _history_row(db, message_id):
    row = db.fetch_one(
        "SELECT old_content, old_content_encrypted FROM msg_edit_history "
        "WHERE message_id = ?",
        (message_id,),
    )
    if row is None:
        return None
    if isinstance(row, dict):
        return row["old_content"], row["old_content_encrypted"]
    return row[0], row[1] if len(row) > 1 else None


@pytest.mark.messaging
class TestEditHistoryEncryptionFailClosed:
    def test_encryption_failure_does_not_store_plaintext(
        self, db, messaging_manager, two_users, monkeypatch
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "body")

        secret = "very sensitive prior content 42"

        def _fail_encrypt(value):
            raise RuntimeError("keyring unavailable")

        monkeypatch.setattr(edit_history_service, "encrypt_data", _fail_encrypt)

        svc = EditHistoryService(db, _AllowAllParticipants(), encryption_enabled=True)
        assert svc.record_edit(user1.id, msg.id, secret) is True

        old_content, encrypted = _history_row(db, msg.id)
        assert encrypted is None
        assert secret not in str(old_content)
        assert old_content == "[unavailable]"

    def test_encryption_success_keeps_marker_and_blob(
        self, db, messaging_manager, two_users, monkeypatch
    ):
        """Normal operation is unchanged: marker in the column, blob alongside."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "body")
        secret = "another sensitive body"

        monkeypatch.setattr(
            edit_history_service, "encrypt_data", lambda value: "FAKE-BLOB"
        )

        svc = EditHistoryService(db, _AllowAllParticipants(), encryption_enabled=True)
        assert svc.record_edit(user1.id, msg.id, secret) is True

        old_content, encrypted = _history_row(db, msg.id)
        assert old_content == "[encrypted]"
        assert encrypted == "FAKE-BLOB"
        assert secret not in str(old_content)

    def test_encryption_disabled_still_stores_plaintext(
        self, db, messaging_manager, two_users, monkeypatch
    ):
        """When encryption is intentionally off, plaintext storage is allowed."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "body")
        secret = "plaintext by policy"

        def _fail_encrypt(value):
            raise AssertionError("encrypt_data must not be called when disabled")

        monkeypatch.setattr(edit_history_service, "encrypt_data", _fail_encrypt)

        svc = EditHistoryService(db, _AllowAllParticipants(), encryption_enabled=False)
        assert svc.record_edit(user1.id, msg.id, secret) is True

        old_content, encrypted = _history_row(db, msg.id)
        assert old_content == secret
        assert encrypted is None
