"""
Targeted coverage tests for behaviours that were previously untested or
only covered by weak placeholders.

Each test asserts a concrete, deterministic property of the real
implementation (no wall-clock timing, no try/except-pass, no mock-only
assertions).
"""

import pytest

import utils.config as config

from src.core.auth.tokens import hash_token, verify_token_hash
from src.core.presence import InvalidStatusError, UserStatus


class TestAuthBindingDefaults:
    """Token binding defaults: binding is OFF unless configured."""

    def test_token_verifies_across_ips_by_default(self, auth_manager, test_user):
        """With token_binding disabled (the default), a token verified from a
        different IP still succeeds."""
        result = auth_manager.login(
            username=test_user.username,
            password="TestPass123!",
            ip_address="192.168.1.1",
            user_agent="DefaultUA/1",
        )

        token_info = auth_manager.verify_token(
            result.token, ip_address="10.0.0.9", user_agent="OtherUA/1"
        )
        assert token_info.valid
        assert token_info.user_id == test_user.id

    def test_missing_binding_config_does_not_gate_verify(self, auth_manager, test_user):
        """No security/token_binding key => verification is not gated."""
        manager = auth_manager._manager
        assert manager._config.get("security", {}).get("token_binding", False) is False

        result = auth_manager.login(test_user.username, "TestPass123!")
        token_info = auth_manager.verify_token(result.token)
        assert token_info.valid


class TestAuthTokenHashVerification:
    """Token secret hashing and verification."""

    def test_hash_token_is_deterministic(self):
        h1 = hash_token("secret-value-123")
        h2 = hash_token("secret-value-123")
        assert h1 == h2
        assert len(h1) > 0

    def test_verify_matching_secret(self):
        secret = "secret-value-123"
        assert verify_token_hash(secret, hash_token(secret)) is True

    def test_verify_wrong_secret(self):
        secret = "secret-value-123"
        assert verify_token_hash("secret-value-124", hash_token(secret)) is False

    def test_verify_length_mismatch(self):
        assert verify_token_hash("short", hash_token("a-much-longer-secret")) is False


class TestPresenceLifecycle:
    """Presence status lifecycle with real assertions."""

    def test_default_status_is_offline(self, presence_manager, test_user):
        status = presence_manager.get_status(999999999)
        assert status == UserStatus.OFFLINE

    def test_set_and_get_status_round_trip(self, presence_manager, test_user):
        presence_manager.set_status(test_user.id, UserStatus.ONLINE)
        assert presence_manager.get_status(test_user.id) == UserStatus.ONLINE

        presence_manager.set_status(test_user.id, UserStatus.DND)
        assert presence_manager.get_status(test_user.id) == UserStatus.DND

    def test_clear_status_returns_offline(self, presence_manager, test_user):
        presence_manager.set_status(test_user.id, UserStatus.ONLINE)
        presence_manager.clear_status(test_user.id)
        assert presence_manager.get_status(test_user.id) == UserStatus.OFFLINE

    def test_invalid_status_string_rejected(self, presence_manager, test_user):
        with pytest.raises(InvalidStatusError):
            presence_manager.set_status(test_user.id, "definitely-not-a-status")

    def test_invalid_status_type_rejected(self, presence_manager, test_user):
        with pytest.raises(InvalidStatusError):
            presence_manager.set_status(test_user.id, 12345)


class TestSearchIndexRoundTrip:
    """Indexing lifecycle: index -> searchable -> removed."""

    def test_index_search_remove_round_trip(self, db_and_search):
        db, auth, messaging, servers, search = db_and_search
        probe_id = 777001
        probe_term = "unique_probe_term_xyz"

        search.index_message(
            message_id=probe_id,
            content=f"hello {probe_term} world",
            metadata={"author_id": 1, "conversation_id": 1},
        )

        row = db.fetch_one(
            "SELECT COUNT(*) AS c FROM search_message_index WHERE message_id = ?",
            (probe_id,),
        )
        assert row["c"] == 1

        results = search.search_messages(1, probe_term, conversation_id=1)
        assert isinstance(results, list)

        search.remove_from_index(probe_id)
        row2 = db.fetch_one(
            "SELECT COUNT(*) AS c FROM search_message_index WHERE message_id = ?",
            (probe_id,),
        )
        assert row2["c"] == 0

    def test_reindex_same_message_upserts(self, db_and_search):
        db, auth, messaging, servers, search = db_and_search

        search.index_message(
            message_id=777002,
            content="first version",
            metadata={"author_id": 1, "conversation_id": 1},
        )
        search.index_message(
            message_id=777002,
            content="second version",
            metadata={"author_id": 1, "conversation_id": 1},
        )

        row = db.fetch_one(
            "SELECT COUNT(*) AS c FROM search_message_index WHERE message_id = ?",
            (777002,),
        )
        assert row["c"] == 1


class TestRatelimitMemoryStorage:
    """MemoryStorage primitive behaviours."""

    def test_increment_accumulates(self, memory_storage):
        assert memory_storage.increment("rl:probe", "count") == 1
        assert memory_storage.increment("rl:probe", "count") == 2
        bucket = memory_storage.get_bucket("rl:probe")
        assert bucket is not None
        assert bucket["count"] == 2

    def test_lock_acquire_release(self, memory_storage):
        token = memory_storage.acquire_lock("rl:lock")
        assert token is not None

        # Held locks must not be re-acquirable.
        assert memory_storage.acquire_lock("rl:lock", timeout=0.01) is None

        memory_storage.release_lock("rl:lock", token)
        assert memory_storage.acquire_lock("rl:lock") is not None

    def test_delete_and_clear(self, memory_storage):
        memory_storage.increment("rl:a", "count")
        memory_storage.increment("rl:b", "count")

        assert memory_storage.delete_bucket("rl:a") is True
        assert memory_storage.get_bucket("rl:a") is None

        memory_storage.clear_all()
        assert memory_storage.get_bucket("rl:b") is None


class TestConfigIsolation:
    """The autouse config-restore fixture must contain per-test mutations.

    NOTE: part1/part2 intentionally rely on in-file definition order (pytest
    runs them in that order and the autouse restore runs between them).
    """

    def test_config_mutation_isolated_part1(self):
        config.set("probe.isolation", "leaked")
        assert config.get("probe.isolation") == "leaked"

    def test_config_mutation_isolated_part2(self):
        # Part1 mutated the config; the autouse restore must have reverted it.
        assert config.get("probe.isolation") is None
