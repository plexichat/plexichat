"""Regression tests for selftest privileged-artifact cleanup hardening.

Covers three residue/tamper weaknesses:

* an unscoped ``admin_role_assignments`` delete let a forged or stale journal
  entry strip a real admin's roles;
* a partially applied reconcile cleared the journal and forgot the artifact;
* API access tokens created by the selftest (encrypted secret + IP/UA, scoped
  credentials) were orphaned when the run crashed before API cleanup.
"""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.core.selftest.artifact_journal import ArtifactJournal


class _SqliteDB:
    """Minimal DB wrapper over in-memory sqlite for ArtifactJournal.reconcile."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE auth_users (
                id INTEGER PRIMARY KEY, username TEXT, permissions TEXT
            );
            CREATE TABLE admin_users (
                id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT
            );
            CREATE TABLE admin_role_assignments (
                admin_id INTEGER, role_id INTEGER
            );
            """
        )

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params or ())

    def fetch_one(self, sql, params=None):
        row = self.conn.execute(sql, params or ()).fetchone()
        return row

    def fetch_all(self, sql, params=None):
        return self.conn.execute(sql, params or ()).fetchall()


def _write_journal(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh)


@pytest.mark.unit
class TestJournalReconcileScope:
    def test_forged_entry_cannot_touch_real_admin(self, tmp_path):
        """A tampered user_id naming a real admin must not strip that admin.

        The old role-assignment DELETE keyed only on the journal-supplied id,
        so a forged/recycled id could remove a live admin's roles; the two
        other statements were username-scoped but the role one was not.
        """
        db = _SqliteDB()
        db.execute(
            "INSERT INTO auth_users (id, username, permissions) VALUES (?, ?, ?)",
            (1, "real_admin", '{"admin.*": true}'),
        )
        db.execute(
            "INSERT INTO admin_users (id, username, password_hash) VALUES (?, ?, ?)",
            (1, "real_admin", "hash"),
        )
        db.execute(
            "INSERT INTO admin_role_assignments (admin_id, role_id) VALUES (?, ?)",
            (1, 7),
        )

        path = str(tmp_path / "journal.json")
        _write_journal(
            path,
            [{"type": "permission_grant", "user_id": 1, "recorded_at": 0}],
        )
        journal = ArtifactJournal(path)
        journal.reconcile(db)

        # Real admin is completely untouched.
        assert db.fetch_one(
            "SELECT permissions FROM auth_users WHERE id = 1"
        ) == ('{"admin.*": true}',)
        assert (
            db.fetch_one("SELECT id FROM admin_users WHERE id = 1") is not None
        )
        assert (
            db.fetch_one(
                "SELECT 1 FROM admin_role_assignments WHERE admin_id = 1"
            )
            is not None
        )

    def test_junk_and_wrong_types_are_not_replayed(self, tmp_path):
        """Non-allowlisted types / malformed ids are dropped, never executed."""
        path = str(tmp_path / "journal.json")
        _write_journal(
            path,
            [
                {"type": "totally_unknown", "user_id": 42},
                {"type": "permission_grant", "user_id": "not-an-int"},
                {"type": "elevated_account", "user_id": True},
            ],
        )
        db = _SqliteDB()
        journal = ArtifactJournal(path)
        assert journal.reconcile(db) == 0
        assert journal.pending() == []
        assert ArtifactJournal(path).pending() == []

    def test_legitimate_entry_still_reconciled(self, tmp_path):
        """A genuine selftest grant is fully revoked and the entry cleared."""
        db = _SqliteDB()
        db.execute(
            "INSERT INTO auth_users (id, username, permissions) VALUES (?, ?, ?)",
            (2, "selftest_admin", '{"admin.*": true, "*": true}'),
        )
        db.execute(
            "INSERT INTO admin_users (id, username, password_hash) VALUES (?, ?, ?)",
            (2, "selftest_admin", "hash"),
        )
        db.execute(
            "INSERT INTO admin_role_assignments (admin_id, role_id) VALUES (?, ?)",
            (2, 9),
        )

        path = str(tmp_path / "journal.json")
        _write_journal(
            path,
            [{"type": "elevated_account", "user_id": 2, "username": "selftest_admin"}],
        )
        journal = ArtifactJournal(path)
        assert journal.reconcile(db) == 3

        assert db.fetch_one("SELECT permissions FROM auth_users WHERE id = 2") == (
            "{}",
        )
        assert db.fetch_one("SELECT id FROM admin_users WHERE id = 2") is None
        assert (
            db.fetch_one("SELECT 1 FROM admin_role_assignments WHERE admin_id = 2")
            is None
        )
        assert journal.pending() == []


@pytest.mark.unit
class TestJournalReconcileFailureRetention:
    def test_partial_failure_keeps_entry_for_retry(self, tmp_path):
        """A failed statement must not silently drop the pending artifact."""
        path = str(tmp_path / "journal.json")
        _write_journal(
            path,
            [{"type": "permission_grant", "user_id": 5, "recorded_at": 0}],
        )

        class FailingDB(_SqliteDB):
            def execute(self, sql, params=None):
                if sql.startswith("DELETE FROM admin_role_assignments"):
                    raise RuntimeError("transient DB error")
                return super().execute(sql, params)

        journal = ArtifactJournal(path)
        touched = journal.reconcile(FailingDB())
        assert touched == 2  # the two statements that succeeded
        assert len(journal.pending()) == 1
        # Survives a reload: next run retries the half-applied revoke.
        assert len(ArtifactJournal(path).pending()) == 1

        # Once the DB is healthy the retry clears it.
        healthy = ArtifactJournal(path)
        assert healthy.reconcile(_SqliteDB()) == 3
        assert healthy.pending() == []


@pytest.mark.unit
class TestApiTokenOrphanCleanup:
    def test_delete_all_for_user_purges_created_api_tokens(self):
        """Selftest-created API tokens (with sensitive fields) must be purged."""
        from unittest.mock import patch

        from src.core.selftest.services.cleanup import CleanupService

        class RecordingDB:
            type = "sqlite"
            in_transaction = False

            def __init__(self):
                self.statements = []

            def execute(self, sql, params=None):
                self.statements.append((" ".join(sql.split()), params))

            def fetch_all(self, sql, params=None):
                return []

            def fetch_one(self, sql, params=None):
                return None

            def column_exists(self, table, column):
                return True

        db = RecordingDB()
        ctx = SimpleNamespace(test_user_id=1)
        with patch("src.core.erasure._media_manager", lambda: None):
            CleanupService(ctx).delete_all_for_user(db, 1, purge_sum=False)

        token_deletes = [sql for sql, _ in db.statements if "auth_api_access_token" in sql]
        assert any(
            sql.startswith("DELETE FROM auth_api_access_tokens WHERE created_by = ?")
            for sql in token_deletes
        ), token_deletes
        assert any(
            "auth_api_access_token_scopes" in sql and "created_by = ?" in sql
            for sql in token_deletes
        ), token_deletes
        assert any(
            "auth_api_access_token_events" in sql and "created_by = ?" in sql
            for sql in token_deletes
        ), token_deletes


@pytest.mark.unit
class TestInternalSecretNotLogged:
    def test_docs_header_helper_never_logs_secret_value(self):
        """The docs session helper must not echo any part of the secret.

        Debug logs are routinely collected and shipped off-box; logging even
        the first 8 characters of the shared internal secret reduces its
        entropy and exposes it to log readers. The helper must confirm the
        header was added without revealing the value.
        """
        from unittest.mock import patch

        import src.core.selftest.services.docs as docs_mod
        from src.core.selftest.services.docs import DocsTester

        secret = "f47ac10b58cc4372a5670e02b2c3d479"
        session = SimpleNamespace(headers={})
        ctx = SimpleNamespace(make_session=lambda: session)

        with patch("src.api.get_internal_secret", return_value=secret), patch.object(
            docs_mod.logger, "debug"
        ) as debug:
            DocsTester(ctx)._add_internal_secret_header(session)

        assert session.headers["X-Plexichat-Internal-Secret"] == secret
        messages = " ".join(str(c) for call in debug.call_args_list for c in call)
        assert secret not in messages
        # No slice of the secret (the old bug logged secret[:8]) may appear.
        for n in range(1, len(secret) + 1):
            assert secret[:n] not in messages
