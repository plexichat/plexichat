"""The self-test must be permanently non-destructive.

The self-test is a routine status check that can run against a live
deployment, so its cleanup may only remove rows owned by ``selftest_%``
accounts or resources recorded by the current run. These tests pin that
contract: any whole-table DELETE / TRUNCATE / DROP in the cleanup paths
fails loudly.
"""

import ast
import os
import re
from pathlib import Path

import pytest

CLEANUP_PATH = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "selftest"
    / "services"
    / "cleanup.py"
)

# Tables where ANY unscoped DELETE is destructive (they hold real user data).
SHARED_TABLES = [
    "notif_notifications",
    "user_reports",
    "media_hash_reports",
    "message_reports",
    "feedback",
    "media_blocked_hashes",
    "media_blocked_users",
    "app_bot_requests",
    "auth_api_access_tokens",
    "auth_api_access_token_scopes",
    "poll_polls",
    "poll_options",
    "poll_votes",
    "poll_voters",
    "thread_threads",
    "thread_members",
    "sticker_stickers",
    "sticker_packs",
    "admin_role_assignments",
    "admin_approvals",
    "username_blacklist",
    "plexijoin_connections",
    "plexijoin_inbound_requests",
    "plexijoin_traffic_log",
]

WHOLE_TABLE_DELETE = re.compile(
    r"DELETE\s+FROM\s+(?P<table>\w+)(?!\s)*(?=\s*[\"']?\s*$)", re.IGNORECASE
)
UNBOUNDED_DELETE = re.compile(
    r"DELETE\s+FROM\s+(?P<table>\w+)\s*[\"']?(?=\s*(?:\"|$))", re.IGNORECASE
)


@pytest.mark.unit
class TestCleanupIsNonDestructive:
    def test_cleanup_file_exists(self):
        assert CLEANUP_PATH.exists(), CLEANUP_PATH

    def test_no_unscoped_table_deletes(self):
        """No statement in cleanup.py may DELETE FROM a table unscoped."""
        source = CLEANUP_PATH.read_text()
        tree = ast.parse(source)

        bad = []
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
            ):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                sql = " ".join(first.value.split())
            elif isinstance(first, (ast.List, ast.Tuple)):
                # f-string-free list of statements: check each element
                for elt in first.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        sql = " ".join(elt.value.split())
                        if self._is_unscoped(sql):
                            bad.append((node.lineno, sql))
                continue
            else:
                # Dynamic SQL (f-strings with table names): flag any DELETE
                # built from an iterable of bare table names.
                sql = ast.unparse(first)
                if re.search(
                    r"DELETE\s+FROM\s*\{tbl\}", sql, re.IGNORECASE
                ) or re.search(r"DELETE\s+FROM\s*f\"", sql, re.IGNORECASE):
                    bad.append((node.lineno, sql))
                continue

            if self._is_unscoped(sql):
                bad.append((node.lineno, sql))

        assert not bad, (
            "Unscoped destructive DELETEs in selftest cleanup: "
            + "; ".join(f"line {ln}: {sql}" for ln, sql in bad)
        )

    def _is_unscoped(self, sql: str) -> bool:
        """True if a DELETE statement has no WHERE clause (whole-table)."""
        if not sql.upper().startswith("DELETE"):
            return False
        return not re.search(r"\bWHERE\b", sql, re.IGNORECASE)

    def test_known_shared_tables_never_truncated(self):
        """Regression: the old whole-table truncate list is gone."""
        source = CLEANUP_PATH.read_text()
        for table in SHARED_TABLES:
            # An unscoped f-string or plain DELETE for the table is fatal.
            pattern = re.compile(
                rf"DELETE FROM {table}\s*[\"']?\s*$", re.IGNORECASE | re.MULTILINE
            )
            assert not pattern.search(source), (
                f"Whole-table DELETE FROM {table} found in selftest cleanup"
            )

    def test_all_user_scoped_deletes_reference_selftest_scope(self):
        """User-scoped deletes in pre-test cleanup target selftest_% users only.

        The pre_test_cleanup fetches user IDs from
        ``auth_users WHERE username LIKE 'selftest_%'`` and passes them to
        delete_all_for_user — verify the source query is scoped.
        """
        source = CLEANUP_PATH.read_text()
        assert "username LIKE 'selftest_%'" in source
        # And the file never contains the old guard's env marker.
        assert "PLEXICHAT_SELFTEST_DISPOSABLE_DB" not in source

    def test_db_guard_forbids_whole_table_wipes(self):
        from src.core.selftest.db_guard import assert_non_destructive

        for bad_sql in (
            "DELETE FROM poll_polls",
            "DELETE FROM notif_notifications",
            "TRUNCATE TABLE poll_polls",
            "DROP TABLE poll_polls",
            "DELETE FROM msg_messages",
        ):
            with pytest.raises(ValueError):
                assert_non_destructive(bad_sql)

        # Scoped deletes are fine.
        assert_non_destructive(
            "DELETE FROM poll_polls WHERE id IN (SELECT poll_id FROM "
            "selftest_owned WHERE username LIKE 'selftest_%')"
        )
        assert_non_destructive(
            "DELETE FROM auth_users WHERE id = ? AND username LIKE 'selftest_%'"
        )

    def test_disposable_db_guard_is_gone(self):
        """The disposable-DB escape hatch must not exist anywhere."""
        import subprocess

        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            [
                "grep",
                "-rl",
                "is_disposable_database",
                str(root / "core" / "selftest"),
            ],
            capture_output=True,
            text=True,
        )
        matches = [ln for ln in result.stdout.splitlines() if "__pycache__" not in ln]
        assert not matches, f"disposable-DB guard still present: {matches}"
