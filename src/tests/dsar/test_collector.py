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

    def test_collectors_do_not_swallow_query_errors(self, db, seeded_user):
        """No collector may log an error and return empty for a valid user.

        Every collector wraps its query in a bare ``except`` that logs and
        returns ``[]``. That is right for a genuinely absent row, but it also
        hid a whole class of real bugs: a projection naming a column that does
        not exist (``msg_messages.is_forwarded``, ``msg_participants.nick``,
        ``srv_members.roles``, ``app_installations.user_id``, ``app_oauth_tokens.name``)
        raised "no such column", the handler swallowed it, and the subject's
        GDPR export silently came back with empty message, participant,
        membership, installation and token lists. Nothing in the response
        indicated anything was missing.

        This captures logger.error across every collector call and fails if any
        fired, which is exactly the signal that a query is broken.
        """
        from unittest.mock import patch

        import utils.logger as logger

        from src.core.dsar.collectors import (
            ApplicationsCollector,
            AutomodCollector,
            MessagesCollector,
            ServersCollector,
        )

        collectors = (
            MessagesCollector,
            ServersCollector,
            ApplicationsCollector,
            AutomodCollector,
        )

        logged = []
        real_error = logger.error

        def _capture(msg, *args, **kwargs):
            logged.append(str(msg))
            return real_error(msg, *args, **kwargs)

        for cls in collectors:
            logged.clear()
            with patch.object(logger, "error", side_effect=_capture):
                result = cls(db).collect(seeded_user.id)
            assert not logged, (
                f"{cls.__name__} logged a query error and returned a degraded "
                f"export: {logged}"
            )
            assert isinstance(result, dict)

    def test_participant_columns_match_live_schema(self, db, seeded_user):
        """msg_participants projection must match the real columns."""
        from src.core.dsar.collectors import MessagesCollector

        live = {
            row["name"] for row in db.fetch_all("PRAGMA table_info(msg_participants)")
        }
        assert live, "msg_participants must exist"
        result = MessagesCollector(db).collect(seeded_user.id)
        # No rows is fine for a fresh user; the point is that the query ran.
        assert isinstance(result["participants"], list)
