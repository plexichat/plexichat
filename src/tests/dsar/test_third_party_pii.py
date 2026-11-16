"""Third-party PII redaction in DSAR exports.

A subject may see the *fact* of a relationship, but not another person's
username/email. These tests exercise the recursive scrubber and its wiring
into the collector, including the legally-required opt-out.
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
            username=f"pii_alice_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        bob = auth_manager.register(
            username=f"pii_bob_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return db, alice, bob


class TestRecursiveScrubber:
    def test_scrubs_nested_fields_and_inline_mentions(self):
        from src.core.dsar.privacy import REDACTED, scrub_third_party_pii

        payload = {
            "relationships": {
                "friends": [
                    {
                        "user_id": 1,
                        "friend_username": "BobbyTables",
                        "friend_email": "bob@example.test",
                    }
                ]
            },
            "messages": {
                "messages": [
                    {"author_id": 1, "content": "hey @BobbyTables, cc bob@example.test"}
                ]
            },
        }
        original = json.dumps(payload, sort_keys=True)
        scrubbed = scrub_third_party_pii(payload)

        friend = scrubbed["relationships"]["friends"][0]
        assert friend["friend_username"] == REDACTED
        assert friend["friend_email"] == REDACTED
        content = scrubbed["messages"]["messages"][0]["content"]
        assert "BobbyTables" not in content
        assert "bob@example.test" not in content
        assert REDACTED in content
        # Input is never mutated.
        assert json.dumps(payload, sort_keys=True) == original

    def test_legally_required_fields_are_preserved(self):
        from src.core.dsar.privacy import REDACTED, scrub_third_party_pii

        payload = {
            "reports": [
                {"reporter_username": "ReporterOne", "reporter_email": "r@x.test"}
            ]
        }
        default = scrub_third_party_pii(payload)
        assert default["reports"][0]["reporter_username"] == REDACTED

        kept = scrub_third_party_pii(payload, legally_required=["reporter_username"])
        assert kept["reports"][0]["reporter_username"] == "ReporterOne"
        assert kept["reports"][0]["reporter_email"] == REDACTED

    def test_subject_own_fields_are_untouched(self):
        from src.core.dsar.privacy import scrub_third_party_pii

        identity = {"id": 7, "username": "alice", "email_index": "abc"}
        assert scrub_third_party_pii(identity) == identity


class TestCollectorScrubbing:
    def test_friend_username_not_in_export(self, two_users):
        db, alice, bob = two_users
        now = int(time.time())
        db.execute(
            "INSERT INTO rel_friends (user_id, friend_id, created_at) VALUES (?, ?, ?)",
            (alice.id, bob.id, now),
        )
        conv_id = int(time.time_ns() % 10**12)
        db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, owner_id,"
            " created_at, updated_at) VALUES (?, 'dm', ?, ?, ?)",
            (conv_id, alice.id, now, now),
        )
        db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                conv_id + 1,
                conv_id,
                alice.id,
                f"hi {bob.username}",
                now,
                now,
            ),
        )

        from src.core.dsar.collector import DataCollector

        export = DataCollector(db).collect_all(alice.id)
        friends = export["relationships"]["friends"]
        assert friends and friends[0]["friend_username"] == "[redacted]"
        payload = json.dumps(export, default=str)
        assert bob.username not in payload
        # The subject's own identity is still present and readable.
        assert alice.username in payload

    def test_legally_required_config_opt_in(self, two_users, monkeypatch):
        db, alice, bob = two_users
        db.execute(
            "INSERT INTO rel_friends (user_id, friend_id, created_at) VALUES (?, ?, ?)",
            (alice.id, bob.id, int(time.time())),
        )
        import src.core.dsar.collector as collector_module

        monkeypatch.setattr(
            collector_module,
            "legally_required_pii_fields",
            lambda: ["friend_username"],
        )
        export = collector_module.DataCollector(db).collect_all(alice.id)
        assert export["relationships"]["friends"][0]["friend_username"] == bob.username
