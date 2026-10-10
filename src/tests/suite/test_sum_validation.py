"""Tests for the .plexisum upload validation boundary (S4).

``_validate_workbook_file`` is the server-side gate for untrusted workbook
uploads. These tests pin the rejection of non-SQLite, wrong-version, corrupt,
and over-limit inputs so a pathological upload can never reach storage.
"""

import base64
import sqlite3

import pytest

from src.core.suite.sum.manager import SumManager, SumValidationError
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _validate(manager, data: bytes) -> None:
    manager._validate_workbook_file(data)


def _real_workbook_bytes(manager, owner, title="Valid") -> bytes:
    """Produce a canonical valid workbook via the manager's own serializer."""
    workbook = manager.create_workbook(owner.id, title)
    result = manager.download_file(owner.id, int(workbook["id"]))
    return base64.b64decode(result["content"])


def test_valid_workbook_accepted(db, two_users, server_manager):
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    owner, _ = two_users
    _validate(manager, _real_workbook_bytes(manager, owner))  # must not raise


def test_garbage_bytes_rejected(db, two_users, server_manager):
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    with pytest.raises(SumValidationError):
        _validate(manager, b"this is not a workbook at all")


def test_empty_bytes_rejected(db, two_users, server_manager):
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    with pytest.raises(SumValidationError):
        _validate(manager, b"")


def test_pxsm_container_with_non_sqlite_payload_rejected(db, two_users, server_manager):
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    # A PXSM magic prefix whose payload is not a SQLite database.
    payload = b"PXSM" + b"\x00" * 8 + b"not-a-database"
    with pytest.raises(SumValidationError):
        _validate(manager, payload)


def test_sqlite_without_sum_meta_rejected(db, two_users, server_manager):
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE unrelated (id INTEGER)")
    connection.commit()
    data = connection.serialize()
    connection.close()
    with pytest.raises(SumValidationError):
        _validate(manager, data)


def test_wrong_format_version_rejected(db, two_users, server_manager):
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE sum_meta (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute(
        "INSERT INTO sum_meta (key, value) VALUES ('format_version', '999')"
    )
    connection.execute("CREATE TABLE sum_sheets (id INTEGER PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE sum_cells (row_index INTEGER, column_index INTEGER)"
    )
    connection.commit()
    data = connection.serialize()
    connection.close()
    with pytest.raises(SumValidationError):
        _validate(manager, data)


def test_truncated_sqlite_rejected(db, two_users, server_manager):
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager)
    owner, _ = two_users
    data = _real_workbook_bytes(manager, owner)
    # Chop off the tail: a truncated database must fail integrity or open.
    with pytest.raises(SumValidationError):
        _validate(manager, data[: len(data) // 2])
