"""Regression test for DSAR bug D2 (metadata serialized as JSON)."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def _make_dsar_env(db, auth_manager, pii_gen):
    from src.core.dsar import setup as dsar_setup
    from unittest.mock import patch
    from src.utils import encryption

    dsar_setup(db)

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="dsard2user",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return db, user


class TestMetadataSerializationD2:
    def test_metadata_stored_as_json_not_repr(self, db, auth_manager, pii_gen):
        from src.core.dsar import request_data_export

        database, user = _make_dsar_env(db, auth_manager, pii_gen)
        result = request_data_export(
            user.id, format="json", categories=["messages", "profile"]
        )
        assert result is not None

        row = database.fetch_one(
            "SELECT metadata FROM dsar_requests WHERE id = ?", (result["id"],)
        )
        assert row is not None
        raw = row["metadata"]
        assert raw is not None
        # D2: previously ``str(metadata)`` produced a Python repr such as
        # "{'categories': [...]}" which is NOT valid JSON. It must round-trip
        # through json.loads instead.
        parsed = json.loads(raw)
        assert parsed == {"categories": ["messages", "profile"]}

    def test_metadata_none_when_no_categories(self, db, auth_manager, pii_gen):
        from src.core.dsar import request_data_export

        database, user = _make_dsar_env(db, auth_manager, pii_gen)
        result = request_data_export(user.id, format="json")
        row = database.fetch_one(
            "SELECT metadata FROM dsar_requests WHERE id = ?", (result["id"],)
        )
        assert row["metadata"] is None


class TestCountRecordsKeyColumn:
    """D2 (count_records): msg_conversations is keyed by owner_id, not user_id."""

    def test_count_uses_owner_id_for_conversations(self, db, auth_manager, pii_gen):
        from src.core.dsar.collector import DataCollector

        database, user = _make_dsar_env(db, auth_manager, pii_gen)

        # A conversation owned by the user must be counted (it previously
        # raised an SQL error because the query used the non-existent
        # ``user_id`` column, under-reporting the user's data).
        database.execute(
            "INSERT INTO msg_conversations (id, conversation_type, owner_id, created_at, updated_at) "
            "VALUES (?, 'dm', ?, 1000, 1000)",
            (7770001, user.id),
        )

        collector = DataCollector(database)
        counts = collector.count_records(user.id)

        key = "messages_msg_conversations"
        assert key in counts
        assert counts[key] >= 1

    def test_count_does_not_raise_for_all_tables(self, db, auth_manager, pii_gen):
        from src.core.dsar.collector import DataCollector

        database, user = _make_dsar_env(db, auth_manager, pii_gen)
        collector = DataCollector(database)
        # Must complete without raising (no missing-column SQL errors).
        counts = collector.count_records(user.id)
        assert isinstance(counts, dict)
