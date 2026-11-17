"""Deep hardening regressions.

- Federation replay-nonce retention must cover the full clock-skew window so a
  near-future timestamp cannot outlive its nonce row and be replayed.
- Destructive-SQL screening of forward migrations must see through SQL comments
  that split a keyword pair (DROP/**/TABLE).
- The cloner's same-database guard must resolve symlinks so an alias of the
  source file cannot be used as a (destructive) clone target.
"""

from __future__ import annotations


import pytest


# ---------------------------------------------------------------------------
# 1. Replay nonce window mismatch (join token replay bypass)
# ---------------------------------------------------------------------------


class _ReplayDB:
    """Minimal DB double for consume_replay_nonce's storage interactions."""

    def __init__(self):
        self.rows: set[tuple[int, str, int]] = set()
        self.cleanup_thresholds: list[int] = []

    def execute(self, sql, params=()):
        if sql.strip().lower().startswith("delete from plexijoin_replay_nonces"):
            threshold = int(params[0])
            self.cleanup_thresholds.append(threshold)
            self.rows = {row for row in self.rows if row[2] >= threshold}
        return None

    def insert_or_ignore(self, table, columns, values):
        key = (int(values[0]), str(values[1]), int(values[2]))
        if key in self.rows:
            return False
        self.rows.add(key)
        return True


def test_replay_nonce_outlives_full_skew_window(monkeypatch):
    from src.core.plexijoin import manager as manager_module

    fake_db = _ReplayDB()
    mgr = manager_module.PlexiJoinManager(fake_db)

    now = 1_700_000_000
    monkeypatch.setattr(manager_module.time, "time", lambda: now)

    # A peer sends a signed request timestamped at the far edge of the allowed
    # skew (now + 300). The signature stays valid until now + 600.
    timestamp = now + 300
    assert mgr.consume_replay_nonce(1, "replaynonce1", timestamp) is True

    # 301 seconds later the request is still inside the +/- 300s window.
    later = now + 301
    monkeypatch.setattr(manager_module.time, "time", lambda: later)
    # Trigger the lazy cleanup with an unrelated fresh nonce.
    assert mgr.consume_replay_nonce(1, "othernonce2", later) is True

    # The original nonce must still be remembered: replaying the same signed
    # request must fail even though the row-pruning cleanup has now run.
    assert mgr.consume_replay_nonce(1, "replaynonce1", timestamp) is False


# ---------------------------------------------------------------------------
# 2. SQL comment injection bypasses the migration safety gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        'db.execute("DROP/**/TABLE users")',
        "db.execute('DROP--note\\nTABLE users')",
        'db.execute("TRUNCATE/*x*/TABLE migrations_history")',
    ],
)
def test_forward_migration_gate_sees_through_sql_comments(sql):
    from src.core.migrations.validator import validate_migration_up_sql

    with pytest.raises(ValueError, match="Dangerous SQL"):
        validate_migration_up_sql(sql)


def test_general_sql_safety_sees_through_sql_comments():
    from src.core.migrations.validator import validate_sql_safety

    with pytest.raises(ValueError, match="Dangerous SQL"):
        validate_sql_safety("DROP/**/DATABASE production")


def test_bare_delete_gate_sees_through_sql_comments():
    from src.core.migrations.validator import validate_migration_up_sql

    with pytest.raises(ValueError, match="Dangerous SQL"):
        validate_migration_up_sql('db.execute("DELETE/*x*/FROM users")')


# ---------------------------------------------------------------------------
# 3. Cloner same-database guard bypass via symlinked path
# ---------------------------------------------------------------------------


class _FakeDB:
    def __init__(self, path: str, db_type: str = "sqlite"):
        self.type = db_type
        self.config = {"type": db_type, "path": path}


def test_cloner_rejects_symlinked_alias_of_source(tmp_path):
    from src.core.migrations.cloner import DataCloner

    real_db = tmp_path / "live.db"
    real_db.write_bytes(b"")
    alias = tmp_path / "alias.db"
    alias.symlink_to(real_db)

    source = _FakeDB(str(real_db))
    target = _FakeDB(str(alias))

    # abspath saw two different paths and allowed cloning; clone_table() would
    # then DELETE FROM every source table via the alias. realpath collapses the
    # symlink and the guard must refuse.
    with pytest.raises(ValueError, match="same database"):
        DataCloner(source, target)


def test_cloner_still_allows_distinct_databases(tmp_path):
    from src.core.migrations.cloner import DataCloner

    source = _FakeDB(str(tmp_path / "source.db"))
    target = _FakeDB(str(tmp_path / "target.db"))
    cloner = DataCloner(source, target)
    assert cloner.source is source
    assert cloner.target is target
