"""Cross-user leakage guard for DSAR exports.

A DSAR export must contain only the requesting subject's data. These tests
seed two unrelated users and verify that collecting for one never surfaces
the other's rows, and that the runtime guard rejects a foreign-owned row.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def two_users(db, auth_manager, pii_gen):
    from unittest.mock import patch

    from src.core.dsar import setup as dsar_setup
    from src.utils import encryption

    dsar_setup(db)
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        alice = auth_manager.register(
            username=f"scope_alice_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        bob = auth_manager.register(
            username=f"scope_bob_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return db, alice, bob


class TestCrossUserLeakage:
    def test_export_for_alice_contains_no_bob_rows(self, two_users):
        db, alice, bob = two_users

        now = int(time.time())
        conv_id = int(time.time_ns() % 10**12)
        msg_id = conv_id + 1
        bob_secret = f"BOB_PRIVATE_{uuid.uuid4().hex}"
        bob_token = f"BOB_TOKEN_{uuid.uuid4().hex}"
        db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, owner_id,"
            " created_at, updated_at) VALUES (?, 'dm', ?, ?, ?)",
            (conv_id, bob.id, now, now),
        )
        db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, conv_id, bob.id, bob_secret, now, now),
        )
        db.execute(
            "INSERT INTO auth_sessions (user_id, token_hash, ip_encrypted,"
            " ua_encrypted, created_at, expires_at, last_activity)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bob.id, bob_token, "CIPHER", "CIPHER", now, now + 3600, now),
        )

        from src.core.dsar.collector import DataCollector

        export = DataCollector(db).collect_all(alice.id)

        assert export["user_id"] == alice.id
        assert export["identity"]["id"] == alice.id
        payload = json.dumps(export, default=str)
        assert bob_secret not in payload
        assert bob_token not in payload
        assert bob.username not in payload
        assert bob.email not in payload

    def test_guard_rejects_foreign_owned_row(self):
        from src.core.dsar.privacy import (
            ExportScopeViolation,
            verify_export_scope,
        )

        alice_id = 1001
        bob_id = 2002
        leaked = {
            "user_id": alice_id,
            "identity": {"id": alice_id},
            "messages": {
                "messages": [{"id": 1, "author_id": bob_id, "content": "bob's secret"}]
            },
        }
        with pytest.raises(ExportScopeViolation):
            verify_export_scope(leaked, alice_id)

    def test_guard_accepts_subject_referenced_row(self):
        """Relationship rows may point at other users alongside the subject."""
        from src.core.dsar.privacy import verify_export_scope

        alice_id = 1001
        bob_id = 2002
        export = {
            "user_id": alice_id,
            "relationships": {
                "friends": [{"user_id": alice_id, "friend_id": bob_id}],
                "incoming_friend_requests": [
                    {"recipient_id": alice_id, "sender_id": bob_id}
                ],
            },
        }
        # Must not raise: each row references the subject in an owner field.
        verify_export_scope(export, alice_id)

    def test_guard_rejects_wrong_envelope_user(self):
        from src.core.dsar.privacy import (
            ExportScopeViolation,
            verify_export_scope,
        )

        with pytest.raises(ExportScopeViolation):
            verify_export_scope({"user_id": 2002}, 1001)
