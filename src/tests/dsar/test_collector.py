"""DSAR DataCollector - 24 collector methods, schema-tolerant."""

from __future__ import annotations

import uuid

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
            username=f"dsaruser_{uuid.uuid4().hex[:8]}",
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

    def test_identity_collector_returns_registered_user(self, db, seeded_user):
        """The identity collector must surface the registered user's row."""
        from src.core.dsar.collectors import IdentityCollector

        result = IdentityCollector(db).collect(seeded_user.id)
        assert result, "identity collect() must return the seeded user"
        assert result["id"] == seeded_user.id
        # Sensitive fields are redacted / never present.
        assert "password_hash" not in result
        assert "totp_secret_encrypted" not in result
        assert "backup_codes_hash" not in result

    def test_unknown_user_collects_empty(self, db):
        """A user without a row must yield an empty dict per collector."""
        from src.core.dsar.collectors import IdentityCollector, SessionsCollector

        assert IdentityCollector(db).collect(999_999_999) == {}
        # Session data is a structured envelope even with no rows.
        sessions = SessionsCollector(db).collect(999_999_999)
        assert sessions == {"devices": [], "known_ips": [], "sessions": []}

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
            # Collectors must never raise for a known user; any exception
            # here is a real schema/query bug that must surface loudly.
            result = collector.collect(seeded_user.id)
            assert isinstance(result, dict)
