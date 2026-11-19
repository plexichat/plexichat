"""Regression tests for the query builder's Pydantic validation boundary.

``QueryBuilder.insert()/update().validate(Model)`` used to call
``Model(**data)`` purely as a check and then execute the *original* dict. That
broke the validation boundary in two ways:

* Pydantic ignores unknown keys by default, so a key the model never declared
  (e.g. a privilege column) passed validation and still reached the SQL.
* Field coercion / before-validators ran but their result was discarded, so the
  raw, unvalidated value was written instead of the validated one.

These tests pin both behaviours down for INSERT and UPDATE.
"""

import os
import sqlite3
import tempfile

import pytest
from pydantic import BaseModel, field_validator

from src.core.database.builder import QueryBuilder, ValidationModelError


class AccountInsert(BaseModel):
    username: str
    email: str
    password_hash: str


class AccountUpdate(BaseModel):
    username: str | None = None
    email: str | None = None
    password_hash: str | None = None


class NormalizingInsert(BaseModel):
    """A model whose field validator changes the value it validates."""

    username: str

    @field_validator("username")
    @classmethod
    def uppercase(cls, value: str) -> str:
        return value.upper()


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT,
            password_hash TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()
    try:
        os.unlink(path)
    except OSError:
        pass


def _builder(conn):
    return QueryBuilder(conn, db_type="sqlite", enable_schema_validation=False)


def test_insert_validate_rejects_undeclared_field(db):
    """An extra, unvalidated key must never reach the INSERT statement."""
    with pytest.raises(ValidationModelError, match="unexpected field"):
        (
            _builder(db)
            .table("accounts")
            .insert(
                {
                    "username": "alice",
                    "email": "alice@example.com",
                    "password_hash": "x" * 60,
                    "is_admin": 1,  # not declared by AccountInsert
                }
            )
            .validate(AccountInsert)
        )
    assert db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_insert_validate_uses_validated_value_not_raw_input(db):
    """The coerced value from a field validator is what gets persisted."""
    _builder(db).table("accounts").insert({"username": "alice"}).validate(
        NormalizingInsert
    ).execute()
    row = db.execute("SELECT username FROM accounts").fetchone()
    assert row["username"] == "ALICE"


def test_insert_validate_accepts_declared_fields(db):
    """Valid input is still accepted (no weakening of the check)."""
    result = (
        _builder(db)
        .table("accounts")
        .insert(
            {
                "username": "bob",
                "email": "bob@example.com",
                "password_hash": "y" * 60,
            }
        )
        .validate(AccountInsert)
        .execute()
    )
    assert result == 1
    row = db.execute("SELECT username, email FROM accounts").fetchone()
    assert (row["username"], row["email"]) == ("bob", "bob@example.com")


def test_update_validate_rejects_undeclared_field(db):
    """UPDATE validation must also refuse keys the model never declared."""
    db.execute(
        "INSERT INTO accounts (username, email, password_hash) VALUES (?, ?, ?)",
        ("alice", "alice@example.com", "x" * 60),
    )
    db.commit()
    with pytest.raises(ValidationModelError, match="unexpected field"):
        (
            _builder(db)
            .table("accounts")
            .update({"username": "bob", "is_admin": 1})
            .validate(AccountUpdate)
            .where("username", "=", "alice")
        )
    row = db.execute("SELECT is_admin FROM accounts").fetchone()
    assert row["is_admin"] == 0


def test_update_validate_uses_validated_value_not_raw_input(db):
    """UPDATE persists the validated value, not the raw caller input."""
    db.execute(
        "INSERT INTO accounts (username, email, password_hash) VALUES (?, ?, ?)",
        ("alice", "alice@example.com", "x" * 60),
    )
    db.commit()
    _builder(db).table("accounts").update({"username": "bob"}).validate(
        NormalizingInsert
    ).where("username", "=", "alice").execute()
    row = db.execute("SELECT username FROM accounts").fetchone()
    assert row["username"] == "BOB"
