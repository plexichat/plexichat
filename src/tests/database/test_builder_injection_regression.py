"""Injection regression matrix for every query builder.

For each builder, hostile identifiers (quotes, semicolons, SQL comments and
unicode look-alikes) are rejected before any statement is built, while the same
payloads are safe as *values* because they are bound as parameters and the
target table survives. The migration cloner is included because it builds SQL
for the same identifier classes.
"""

import sqlite3
from typing import cast

import pytest

from src.core.database.core import Database
from src.core.database.builder import QueryBuilder, SQLInjectionError
from src.core.migrations.cloner import DataCloner
from src.core.security.hardening import HardeningViolation

pytestmark = [pytest.mark.unit, pytest.mark.security]

INJECTION_PAYLOADS = [
    "'",
    ";",
    "'; DROP TABLE users; --",
    "' OR '1'='1",
    "/* comment */",
    "--",
    "x'); DELETE FROM users; --",
    "üsers",  # non-ASCII letters
    "users\u200b",  # zero-width space
    "users\uff1b",  # fullwidth semicolon
]


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, "
        "email TEXT, password_hash TEXT)"
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def builder(db):
    return QueryBuilder(db, db_type="sqlite")


# --- identifiers: every builder must reject hostile names -------------------


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_insert_rejects_hostile_column(builder, payload):
    with pytest.raises(SQLInjectionError):
        builder.table("users").insert({payload: "value"}).build()


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_select_rejects_hostile_table_and_column(builder, payload):
    with pytest.raises(SQLInjectionError):
        builder.table(payload).select().build()
    with pytest.raises(SQLInjectionError):
        builder.table("users").select([payload]).build()
    with pytest.raises(SQLInjectionError):
        builder.table("users").select().where(payload, "=", 1).build()


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_update_rejects_hostile_table_column_and_where(builder, payload):
    with pytest.raises(SQLInjectionError):
        builder.table(payload).update({"username": "x"}).where("id", "=", 1).build()
    with pytest.raises(SQLInjectionError):
        builder.table("users").update({payload: "x"}).where("id", "=", 1).build()
    with pytest.raises(SQLInjectionError):
        builder.table("users").update({"username": "x"}).where(payload, "=", 1).build()


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_delete_rejects_hostile_table_and_where(builder, payload):
    with pytest.raises(SQLInjectionError):
        builder.table(payload).delete().where("id", "=", 1).build()
    with pytest.raises(SQLInjectionError):
        builder.table("users").delete().where(payload, "=", 1).build()


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_where_rejects_hostile_operator(builder, payload):
    with pytest.raises(ValueError):
        builder.table("users").select().where("id", payload, 1).build()


# --- values: the same payloads are inert when bound -------------------------


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_inserted_values_are_bound_and_table_survives(builder, db, payload):
    builder.table("users").insert(
        {"username": payload, "email": "e", "password_hash": "h"}
    ).execute()

    rows = builder.table("users").select().where("username", "=", payload).execute()
    assert len(rows) == 1
    assert rows[0]["username"] == payload
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_update_and_delete_values_are_bound(builder, db, payload):
    builder.table("users").insert(
        {"username": "orig", "email": "e", "password_hash": "h"}
    ).execute()

    updated = (
        builder.table("users")
        .update({"username": payload})
        .where("username", "=", "orig")
        .execute()
    )
    assert updated == 1
    deleted = builder.table("users").delete().where("username", "=", payload).execute()
    assert deleted == 1
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


# --- cloner: hostile table names never reach a statement --------------------


class _FakeDB:
    def __init__(self, path):
        self.type = "sqlite"
        self.config = {"type": "sqlite", "path": path}

    def fetch_all(self, *args, **kwargs):
        raise AssertionError("no statement should run for a hostile name")


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_cloner_rejects_hostile_table_names(payload):
    cloner = DataCloner(
        cast(Database, _FakeDB("/tmp/a.db")), cast(Database, _FakeDB("/tmp/b.db"))
    )
    with pytest.raises(HardeningViolation):
        cloner.clone_table(payload)
