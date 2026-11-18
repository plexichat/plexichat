"""SEC300 invariant suite: selftest privileged-artifact / journal / guard hardening.

Deterministic, self-contained regression lock on the recent selftest security
fixes (comment-bypass denylist, symlink-safe journal writes, scoped + retentive
journal reconciliation, API-token orphan cleanup and the config boundary).

The suite imports only the guard modules and uses in-memory SQLite plus
``tmp_path``; it needs no running app, database, network or fixture ordering,
so it cannot flake. Every assertion pins an invariant that a past regression
violated -- weakening any of the guards below will fail this suite.
"""

import json
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]


# ---------------------------------------------------------------------------
# SQL non-destructive guard (comment bypass / trailing statements)
# ---------------------------------------------------------------------------

_DESTRUCTIVE_COMMENT_SQL = [
    # comments used as keyword separators
    "DELETE/**/FROM/**/auth_users",
    "DELETE/*x*/FROM/*x*/auth_users;",
    "DELETE /* c */ FROM auth_users",
    "DELETE -- c\nFROM auth_users",
    "DELETE FROM/* c */auth_users -- looks scoped?",
    # a WHERE that exists only inside a comment is not a scope restriction
    "DELETE FROM t /* WHERE id = ? */",
    "DELETE FROM t -- WHERE id = ?",
    # trailing statement terminators are still whole-table wipes
    "DELETE FROM t;",
    "DELETE FROM t; SELECT 1",
    # unterminated block comment swallows the tail
    "DELETE FROM t /* unterminated",
    # other destructive verbs hidden behind comments
    "DROP/**/TABLE/**/auth_users",
    "TRUNCATE/*x*/auth_users",
]


@pytest.mark.parametrize("statement", _DESTRUCTIVE_COMMENT_SQL)
def test_comment_hidden_destructive_sql_is_rejected(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    with pytest.raises(ValueError, match="destructive statement"):
        assert_non_destructive(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM auth_users WHERE note = 'a -- b /* c */' AND id = ?",
        "DELETE FROM auth_api_access_tokens WHERE created_by = ?",
        "DELETE FROM auth_users WHERE id = ? AND username LIKE 'selftest_%'",
        "UPDATE auth_users SET permissions = '{}' WHERE id = ?",
        "DELETE FROM admin_role_assignments WHERE admin_id = ? AND admin_id IN "
        "(SELECT id FROM auth_users WHERE username LIKE 'selftest_%')",
    ],
)
def test_genuinely_scoped_sql_is_accepted(statement):
    """Comment-shaped text inside a literal must not disable scoped deletes."""
    from src.core.selftest.db_guard import assert_non_destructive

    assert_non_destructive(statement)


def test_normalize_sql_strips_comments_as_whitespace():
    from src.core.selftest.db_guard import _normalize_sql

    assert (
        _normalize_sql("DELETE/**/FROM/* c */auth_users -- trailing")
        == "DELETE FROM auth_users"
    )
    assert _normalize_sql("DROP/*x*/TABLE/*x*/t") == "DROP TABLE t"


# ---------------------------------------------------------------------------
# Journal filesystem invariants (traversal / symlink / mode)
# ---------------------------------------------------------------------------

_TAMPERED_PATHS = [
    "../../etc/passwd",
    "data/../../../etc/passwd",
    "/etc/../etc/passwd",
    "data\\..\\journal.json",  # Windows separators are normalized too
    "data/journal\x00.json",
    "",
]


@pytest.mark.parametrize("bad_path", _TAMPERED_PATHS)
def test_journal_rejects_traversal_or_nul_paths(bad_path):
    from src.core.selftest.artifact_journal import ArtifactJournal, _DEFAULT_PATH

    assert ArtifactJournal(bad_path).path == _DEFAULT_PATH


@pytest.mark.parametrize("bad_path", [None, 123, b"data/journal.json"])
def test_journal_rejects_non_string_paths(bad_path):
    from src.core.selftest.artifact_journal import ArtifactJournal, _DEFAULT_PATH

    assert ArtifactJournal(bad_path).path == _DEFAULT_PATH


def test_journal_flush_ignores_planted_tmp_symlink_and_leaves_no_temp(tmp_path):
    """A planted ``<journal>.tmp`` symlink must not redirect the write.

    The old writer used a predictable ``path + ".tmp"`` name with
    ``open(..., "w")`` (symlink-following). The hardened writer uses mkstemp
    (O_CREAT | O_EXCL, 0o600) plus atomic os.replace, so the symlink is never
    followed and no predictable temp file survives a flush.
    """
    from src.core.selftest.artifact_journal import ArtifactJournal

    journal_path = tmp_path / "journal.json"
    victim = tmp_path / "victim.txt"
    victim.write_text("DO NOT TOUCH", encoding="utf-8")

    planted = tmp_path / "journal.json.tmp"
    planted.symlink_to(victim)

    journal = ArtifactJournal(str(journal_path))
    journal.record("permission_grant", user_id=7, permissions=["admin.*"])

    assert victim.read_text(encoding="utf-8") == "DO NOT TOUCH"
    assert planted.is_symlink()

    entries = json.loads(journal_path.read_text(encoding="utf-8"))
    assert entries[0]["user_id"] == 7
    assert entries[0]["type"] == "permission_grant"

    leftovers = [
        p.name
        for p in tmp_path.iterdir()
        if p.name.startswith(".selftest_artifact_journal.")
    ]
    assert leftovers == []


def test_journal_file_is_private_and_clear_empties_it(tmp_path):
    from src.core.selftest.artifact_journal import ArtifactJournal

    journal_path = tmp_path / "journal.json"
    journal = ArtifactJournal(str(journal_path))
    journal.record("elevated_account", user_id=9, username="selftest_admin")

    if os.name == "posix":
        assert stat.S_IMODE(os.stat(journal_path).st_mode) == 0o600

    journal.clear()
    assert json.loads(journal_path.read_text(encoding="utf-8")) == []
    assert ArtifactJournal(str(journal_path)).pending() == []


# ---------------------------------------------------------------------------
# Journal reconciliation invariants (forgery / residue / partial failure)
# ---------------------------------------------------------------------------


class _SqliteDB:
    """Minimal DB wrapper over in-memory SQLite for ArtifactJournal.reconcile."""

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
            CREATE TABLE admin_role_assignments (admin_id INTEGER, role_id INTEGER);
            """
        )

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params or ())

    def fetch_one(self, sql, params=None):
        return self.conn.execute(sql, params or ()).fetchone()

    def fetch_all(self, sql, params=None):
        return self.conn.execute(sql, params or ()).fetchall()


def _write_journal(path, entries):
    Path(path).write_text(json.dumps(entries), encoding="utf-8")


@pytest.mark.parametrize("entry_type", ["permission_grant", "elevated_account"])
def test_forged_entry_cannot_revoke_a_real_admin(tmp_path, entry_type):
    """Every revoke statement is scoped to selftest_% accounts.

    A forged/recycled user_id naming a live admin must not strip that admin's
    permissions, admin_users row or role assignments.
    """
    from src.core.selftest.artifact_journal import ArtifactJournal

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
    _write_journal(path, [{"type": entry_type, "user_id": 1, "recorded_at": 0}])
    ArtifactJournal(path).reconcile(db)

    assert db.fetch_one("SELECT permissions FROM auth_users WHERE id = 1") == (
        '{"admin.*": true}',
    )
    assert db.fetch_one("SELECT id FROM admin_users WHERE id = 1") is not None
    assert (
        db.fetch_one("SELECT 1 FROM admin_role_assignments WHERE admin_id = 1")
        is not None
    )


def test_junk_and_wrong_types_are_dropped_never_replayed(tmp_path):
    from src.core.selftest.artifact_journal import ArtifactJournal

    path = str(tmp_path / "journal.json")
    _write_journal(
        path,
        [
            {"type": "totally_unknown", "user_id": 42},
            {"type": "permission_grant", "user_id": "not-an-int"},
            {"type": "elevated_account", "user_id": True},
            {"type": "elevated_account", "user_id": 0},
            {"type": "elevated_account", "user_id": -5},
            {"type": "permission_grant"},  # missing user_id
        ],
    )
    journal = ArtifactJournal(path)
    assert journal.reconcile(_SqliteDB()) == 0
    assert journal.pending() == []
    assert ArtifactJournal(path).pending() == []


def test_legitimate_selftest_entry_is_fully_reconciled(tmp_path):
    from src.core.selftest.artifact_journal import ArtifactJournal

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

    assert db.fetch_one("SELECT permissions FROM auth_users WHERE id = 2") == ("{}",)
    assert db.fetch_one("SELECT id FROM admin_users WHERE id = 2") is None
    assert (
        db.fetch_one("SELECT 1 FROM admin_role_assignments WHERE admin_id = 2") is None
    )
    assert journal.pending() == []


def test_partial_failure_retains_entry_for_retry(tmp_path):
    """A failed revoke must stay pending; the next run retries it."""
    from src.core.selftest.artifact_journal import ArtifactJournal

    path = str(tmp_path / "journal.json")
    _write_journal(
        path, [{"type": "permission_grant", "user_id": 5, "recorded_at": 0}]
    )

    class FailingDB(_SqliteDB):
        def execute(self, sql, params=None):
            if sql.startswith("DELETE FROM admin_role_assignments"):
                raise RuntimeError("transient DB error")
            return super().execute(sql, params)

    journal = ArtifactJournal(path)
    assert journal.reconcile(FailingDB()) == 2
    assert len(journal.pending()) == 1
    # Survives a reload: the half-applied revoke is retried next run.
    assert len(ArtifactJournal(path).pending()) == 1

    healthy = ArtifactJournal(path)
    assert healthy.reconcile(_SqliteDB()) == 3
    assert healthy.pending() == []
    assert ArtifactJournal(path).pending() == []


# ---------------------------------------------------------------------------
# Config boundary invariants
# ---------------------------------------------------------------------------


def test_config_boundary_drops_active_content():
    from src.core.selftest.config_guard import sanitize_selftest_config

    cleaned = sanitize_selftest_config(
        {
            "text": "<script>alert(1)</script>",
            "bad_list": ["ok", "{{ 7*7 }}", "__import__('os')"],
            "parallel_get_workers": 4,
        }
    )
    assert "text" not in cleaned
    assert cleaned["bad_list"] == ["ok"]
    assert cleaned["parallel_get_workers"] == 4


def test_config_journal_path_is_confined():
    from src.core.selftest import config_guard
    from src.core.selftest.config_guard import sanitize_selftest_config

    repo_root = Path(config_guard.__file__).resolve().parents[3]
    allowed = repo_root / "data" / "selftest_artifact_journal.json"
    cleaned = sanitize_selftest_config({"journal_path": str(allowed)})
    assert cleaned["journal_path"] == str(allowed.resolve())

    for hostile in ("/etc/passwd", "../../etc/passwd", "~/.ssh/id_rsa/../x"):
        result = sanitize_selftest_config({"journal_path": hostile})
        assert result["journal_path"] == config_guard._DEFAULT_JOURNAL_PATH


def test_config_excluded_endpoints_allowlist_and_workers_bounds():
    from src.core.selftest.config_guard import sanitize_selftest_config

    cleaned = sanitize_selftest_config(
        {
            "excluded_endpoints": [
                "/api/v1/users/{id}",
                "/api/v1/x?a=b&c=d",
                "../etc/passwd",
                "//evil.example",
            ]
        }
    )
    assert cleaned["excluded_endpoints"] == [
        "/api/v1/users/{id}",
        "/api/v1/x?a=b&c=d",
    ]

    for bad in (0, 65, True, "4"):
        assert "parallel_get_workers" not in sanitize_selftest_config(
            {"parallel_get_workers": bad}
        )


def test_config_oversized_collection_is_dropped():
    from src.core.selftest.config_guard import sanitize_selftest_config

    assert "items" not in sanitize_selftest_config({"items": list(range(300))})


# ---------------------------------------------------------------------------
# Cleanup: selftest-created API tokens are purged with their sensitive rows
# ---------------------------------------------------------------------------


def test_delete_all_for_user_purges_api_token_rows():
    from unittest.mock import patch as _patch

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
    with _patch("src.core.erasure._media_manager", lambda: None):
        CleanupService(SimpleNamespace(test_user_id=1)).delete_all_for_user(
            db, 1, purge_sum=False
        )

    token_statements = [
        sql for sql, _ in db.statements if "auth_api_access_token" in sql
    ]
    assert any(
        sql.startswith("DELETE FROM auth_api_access_tokens WHERE created_by = ?")
        for sql in token_statements
    ), token_statements
    assert any(
        "auth_api_access_token_scopes" in sql and "created_by = ?" in sql
        for sql in token_statements
    ), token_statements
    assert any(
        "auth_api_access_token_events" in sql and "created_by = ?" in sql
        for sql in token_statements
    ), token_statements

    # Sensitive child rows are removed before the parent token row so SQLite
    # cleanup (foreign keys OFF) cannot orphan them.
    idx = [i for i, (sql, _) in enumerate(db.statements) if "auth_api_access_token" in sql]
    assert len(idx) == 3


# ---------------------------------------------------------------------------
# Privileged grants are journaled BEFORE the side effect, and rolled back
# ---------------------------------------------------------------------------


def test_permission_grant_is_journaled_before_and_revoked_on_partial_failure():
    import src.api as api
    from src.core.selftest.services.setup.admin import AdminSetupMixin

    events = []

    class FakeJournal:
        def record(self, entry_type, **detail):
            events.append(("record", entry_type, detail))

    class FakeAuth:
        def grant_permission(self, uid, perm, allow_wildcards=False):
            events.append(("grant", perm, allow_wildcards))

    ctx = SimpleNamespace(
        config={"enable_admin_tests": True}, test_user_id=42, journal=FakeJournal()
    )
    setup = AdminSetupMixin(ctx)

    with patch.object(api, "get_auth", return_value=FakeAuth()):
        assert setup.grant_admin_permissions() is True

    assert events[0][0] == "record"
    assert events[0][1] == "permission_grant"
    assert events[0][2]["user_id"] == 42
    assert set(events[0][2]["permissions"]) == {"admin.*", "*"}
    grants = [e for e in events if e[0] == "grant"]
    assert {g[1] for g in grants} == {"admin.*", "*"}
    assert all(g[2] is True for g in grants)  # allow_wildcards opt-in

    # A crash mid-grant revokes what was applied and propagates the error.
    revoked = []
    setup._revoke_grants = lambda uid, granted: revoked.append((uid, list(granted)))

    class FailingAuth(FakeAuth):
        def grant_permission(self, uid, perm, allow_wildcards=False):
            events.append(("grant", perm, allow_wildcards))
            if perm == "*":
                raise RuntimeError("boom")

    with patch.object(api, "get_auth", return_value=FailingAuth()):
        with pytest.raises(RuntimeError, match="boom"):
            setup.grant_admin_permissions()
    assert revoked == [(42, ["admin.*"])]
