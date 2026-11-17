"""Regression: INSERT parameter values must never reach debug logs in plaintext.

``InsertQuery.execute()`` overrode the base executor but built its "sanitized"
log payload from a bare list of raw values. With no column names attached, the
shared sanitizer could not apply its sensitive-key rules, so password hashes,
API tokens and emails were written verbatim to the debug log on every INSERT.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.database.builder.insert import InsertQuery

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _make_insert(data):
    conn = MagicMock()
    conn.cursor.return_value.lastrowid = 7
    return conn, InsertQuery(conn, "users", data, db_type="sqlite")


def test_insert_debug_log_redacts_sensitive_column_values():
    conn, query = _make_insert(
        {
            "email": "victim@example.com",
            "password_hash": "supersecretpassword123",
            "api_token": "tok_live_abcdef0123456789",
            "display_name": "alice",
        }
    )

    with patch("src.core.database.builder.insert.logger.debug") as debug:
        query.execute()

    conn.commit.assert_called_once()
    logged = "\n".join(str(call) for call in debug.call_args_list)

    assert "victim@example.com" not in logged
    assert "supersecretpassword123" not in logged
    assert "tok_live_abcdef0123456789" not in logged
    # The log must stay useful: non-sensitive values are still present.
    assert "alice" in logged


def test_insert_debug_log_redacts_email_in_otherwise_safe_column():
    """Email masking is key-independent and must apply to every column."""
    conn, query = _make_insert({"notes": "contact me at victim@example.com please"})

    with patch("src.core.database.builder.insert.logger.debug") as debug:
        query.execute()

    logged = "\n".join(str(call) for call in debug.call_args_list)
    assert "victim@example.com" not in logged
    assert "contact me at" in logged
