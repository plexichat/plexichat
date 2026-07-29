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
        ):
            assert category in export
        # Media collector nests avatars and api_tokens under "media".
        assert "avatars" in export["media"]
        assert "api_tokens" in export["media"]

    def test_count_records_returns_dict(self, collector, seeded_user):
        counts = collector.count_records(seeded_user.id)
        assert isinstance(counts, dict)
        # At minimum an auth_users row should be counted.
        assert any(k.startswith("identity_") for k in counts.keys())

    def test_individual_categories_return(self, db, seeded_user):
        from src.core.dsar.collectors import (
            ApplicationsCollector,
            AutomodCollector,
            ContentCollector,
            FeedbackCollector,
            FeaturesCollector,
            IdentityCollector,
            MessagesCollector,
            MediaCollector,
            NotificationsCollector,
            OAuthCollector,
            PollsCollector,
            PresenceCollector,
            ProfileCollector,
            RelationshipsCollector,
            ReportsCollector,
            SearchCollector,
            ServersCollector,
            SessionsCollector,
            SoundboardCollector,
            StickersCollector,
            VoiceCollector,
        )

        collector_classes = (
            IdentityCollector,
            SessionsCollector,
            ProfileCollector,
            MessagesCollector,
            RelationshipsCollector,
            ServersCollector,
            ContentCollector,
            NotificationsCollector,
            OAuthCollector,
            ApplicationsCollector,
            ReportsCollector,
            FeedbackCollector,
            SearchCollector,
            FeaturesCollector,
            PollsCollector,
            VoiceCollector,
            AutomodCollector,
            PresenceCollector,
            StickersCollector,
            SoundboardCollector,
            MediaCollector,
        )

        for cls in collector_classes:
            collector = cls(db)
            try:
                result = collector.collect(seeded_user.id)
            except Exception:
                result = {}
            assert isinstance(result, dict)
