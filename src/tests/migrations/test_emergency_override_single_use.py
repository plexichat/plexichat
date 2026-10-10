"""M4 regression: emergency override tokens are truly single-use.

Covers:
- Reusing the same token fails the second time (atomic consume).
- Concurrent double-consume lets exactly one caller through.
- Wrong/missing/expired tokens fail closed (functional, no timing oracle).
- Tokens are stored hashed only; plaintext is never persisted/compared.
- Read-only gate checks (peek) never burn the token.
- The finding's repro: reusing one override token for a second tampered
  migration is blocked.
"""

import threading

import pytest

from src.core.migrations.manager import Migration

ENV_VAR = "EMERGENCY_MIGRATION_OVERRIDE"


def _migration_file(tmp_path, version, table):
    path = tmp_path / f"{version}_m4_probe.py"
    path.write_text(
        "def up(db):\n"
        f'    """M4 probe migration {version}."""\n'
        f'    db.execute("CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY)")\n'
        "\n"
        "def down(db):\n"
        '    """Rollback."""\n'
        f'    db.execute("DROP TABLE IF EXISTS {table}")\n'
    )
    return str(path)


@pytest.mark.migrations
class TestEmergencyOverrideSingleUse:
    """M4: single-use, constant-time, fail-closed emergency override tokens."""

    def test_reuse_fails_second_time(self, migration_manager, monkeypatch):
        tracker = migration_manager.tracker
        tracker.ensure_table_exists()
        token = tracker.generate_emergency_override("m4-reuse", expires_minutes=30)
        monkeypatch.setenv(ENV_VAR, token)

        assert tracker.try_consume_emergency_override_from_env() is True
        assert tracker.try_consume_emergency_override_from_env() is False

        # The row is marked used (auditable), not deleted.
        row = tracker.db.fetch_one(
            "SELECT used_at FROM emergency_override_tokens WHERE token = ?",
            (tracker._hash_token(token),),
        )
        assert row is not None
        used_at = row["used_at"] if isinstance(row, dict) else row[0]
        assert used_at is not None

    def test_concurrent_double_consume_single_winner(
        self, migration_manager, monkeypatch
    ):
        tracker = migration_manager.tracker
        tracker.ensure_table_exists()
        token = tracker.generate_emergency_override("m4-concurrent", expires_minutes=30)
        monkeypatch.setenv(ENV_VAR, token)

        barrier = threading.Barrier(2)
        results = []

        def worker():
            barrier.wait()
            results.append(tracker.try_consume_emergency_override_from_env())

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(results) == [False, True]

    def test_wrong_and_missing_token_fail_closed(self, migration_manager, monkeypatch):
        tracker = migration_manager.tracker
        tracker.ensure_table_exists()
        tracker.generate_emergency_override("m4-wrong", expires_minutes=30)

        monkeypatch.setenv(ENV_VAR, "not-a-real-token")
        assert tracker.try_consume_emergency_override_from_env() is False
        assert tracker.peek_emergency_override_from_env() is False

        monkeypatch.delenv(ENV_VAR)
        assert tracker.try_consume_emergency_override_from_env() is False
        assert tracker.peek_emergency_override_from_env() is False

    def test_expired_token_rejected(self, migration_manager, monkeypatch):
        tracker = migration_manager.tracker
        tracker.ensure_table_exists()
        token = tracker.generate_emergency_override("m4-expired", expires_minutes=-1)
        monkeypatch.setenv(ENV_VAR, token)

        assert tracker.peek_emergency_override_from_env() is False
        assert tracker.try_consume_emergency_override_from_env() is False

    def test_plaintext_never_stored(self, migration_manager):
        tracker = migration_manager.tracker
        tracker.ensure_table_exists()
        token = tracker.generate_emergency_override("m4-hash", expires_minutes=30)

        rows = tracker.db.fetch_all("SELECT token FROM emergency_override_tokens")
        stored = [row["token"] if isinstance(row, dict) else row[0] for row in rows]
        assert token not in stored
        assert tracker._hash_token(token) in stored

    def test_gate_check_does_not_consume(self, migration_manager, monkeypatch):
        tracker = migration_manager.tracker
        tracker.ensure_table_exists()
        token = tracker.generate_emergency_override("m4-peek", expires_minutes=30)
        monkeypatch.setenv(ENV_VAR, token)

        # Read-only checks must leave the token valid for the real execution.
        assert tracker.peek_emergency_override_from_env() is True
        assert tracker.peek_emergency_override_from_env() is True
        assert tracker.try_consume_emergency_override_from_env() is True
        assert tracker.peek_emergency_override_from_env() is False

    def test_second_tampered_migration_replay_blocked(
        self, migration_manager, tmp_path, monkeypatch
    ):
        """Finding repro: one token covers the first tampered migration only."""
        manager = migration_manager
        tracker = manager.tracker
        tracker.ensure_table_exists()
        token = tracker.generate_emergency_override("m4-replay", expires_minutes=30)
        monkeypatch.setenv(ENV_VAR, token)

        migrations = []
        for version in ("990", "991"):
            file_path = _migration_file(tmp_path, version, f"m4_probe_{version}")
            # Record a bogus checksum so the guard sees a tampered file.
            tracker.record_migration_start(version, f"m4-{version}", "0" * 64)
            tracker.record_migration_success(version, 1, None)
            migrations.append(
                Migration(
                    version=version,
                    name=f"m4-{version}",
                    file_path=file_path,
                    checksum="0" * 64,
                )
            )

        first = manager._execute_migration(migrations[0])
        assert first["success"] is True

        with pytest.raises(ValueError, match="BLOCKED"):
            manager._execute_migration(migrations[1])
