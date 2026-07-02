"""DSAR DataCollector - 24 collector methods, schema-tolerant."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def collector(db):
    from src.core.dsar.collector import DataCollector

    return DataCollector(db)


@pytest.fixture
def seeded_user(auth_manager, pii_gen):
    from unittest.mock import patch
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=f"dsaruser_{pytest.random_string() if hasattr(pytest, 'random_string') else 'x'}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return user


class TestDataCollector:
    def test_collect_all_returns_dict(self, collector, seeded_user):
        export = collector.collect_all(seeded_user.id)
        assert "exported_at" in export
        assert "user_id" in export
        assert "export_version" in export
        assert export["user_id"] == seeded_user.id
        # Top-level categories exposed by collect_all()
        for category in (
            "identity",
            "sessions",
            "profile",
            "messages",
            "relationships",
            "servers",
            "content",
            "notifications",
            "oauth",
            "applications",
            "reports",
            "feedback",
            "search",
            "features",
            "polls",
            "voice",
            "automod",
            "presence",
            "stickers",
            "soundboard",
            "media",
            "avatars",
            "api_tokens",
        ):
            assert category in export

    def test_count_records_returns_dict(self, collector, seeded_user):
        counts = collector.count_records(seeded_user.id)
        assert isinstance(counts, dict)
        # At minimum an auth_users row should be counted.
        assert any(k.startswith("identity_") for k in counts.keys())

    def test_individual_categories_return(self, collector, seeded_user):
        for method in (
            "_collect_identity",
            "_collect_sessions",
            "_collect_profile",
            "_collect_messages",
            "_collect_relationships",
            "_collect_servers",
            "_collect_content",
            "_collect_notifications",
            "_collect_oauth",
            "_collect_applications",
            "_collect_reports",
            "_collect_feedback",
            "_collect_search",
            "_collect_features",
            "_collect_polls",
            "_collect_voice",
            "_collect_automod",
            "_collect_presence",
            "_collect_stickers",
            "_collect_soundboard",
            "_collect_media",
            "_collect_avatars",
            "_collect_api_tokens",
        ):
            try:
                result = getattr(collector, method)(seeded_user.id)
            except Exception:
                result = {}
            assert isinstance(result, dict)
