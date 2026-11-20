"""Regression tests: embed field rows are inserted in one batched statement.

``create_embed`` and ``update_embed`` used to issue one ``INSERT INTO
embed_fields`` per field (``for i, field in ...: self._db.execute(...)``).
Since a rich embed may carry up to 25 fields, that is up to 25 separate
round trips per write. Both paths now build the row list and hand it to the
database's ``execute_many`` primitive, collapsing the fan-out to one batched
statement per write while preserving field order and inline flags.

The query-count tests fail against the per-field loop and pass against the
batched version, giving a clean before/after signal. The correctness tests pin
that the batched rows are byte-for-byte equivalent to the old ones.
"""

from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.embeds]


_FIELD_INSERT = "INSERT INTO embed_fields"


def _fields(n):
    return [
        {"name": f"field {i}", "value": f"value {i}", "inline": bool(i % 2)}
        for i in range(n)
    ]


class _FieldInsertCounter:
    """Count embed-field INSERT statements routed through each DB API."""

    def __init__(self, db):
        self.db = db
        self.single = 0
        self.batch = 0

    def __enter__(self):
        self._orig_execute = self.db.execute
        self._orig_execute_many = self.db.execute_many

        def counted_execute(sql, params=(), **kwargs):
            if _FIELD_INSERT in sql:
                self.single += 1
            return self._orig_execute(sql, params, **kwargs)

        def counted_execute_many(sql, params_list, **kwargs):
            if _FIELD_INSERT in sql:
                self.batch += 1
            return self._orig_execute_many(sql, params_list, **kwargs)

        self.db.execute = counted_execute
        self.db.execute_many = counted_execute_many
        return self

    def __exit__(self, *exc):
        self.db.execute = self._orig_execute
        self.db.execute_many = self._orig_execute_many
        return False


def _new_manager(db, auth_manager):
    from src.core import embeds
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="embed_batch_user",
            email="embed_batch@example.com",
            password="TestPass123!",
        )
    embeds.setup(db, None, None)
    return embeds._manager, user


def test_create_embed_batches_field_inserts(db, auth_manager):
    manager, user = _new_manager(db, auth_manager)
    fields = _fields(8)

    with _FieldInsertCounter(db) as counter:
        embed = manager.create_embed(user_id=user.id, title="batched", fields=fields)

    assert counter.single == 0, (
        f"create_embed issued {counter.single} per-field INSERTs; "
        "expected a single execute_many batch"
    )
    assert counter.batch == 1
    assert len(embed.fields) == 8


def test_update_embed_batches_field_inserts(db, auth_manager):
    manager, user = _new_manager(db, auth_manager)
    embed = manager.create_embed(user_id=user.id, title="original", fields=_fields(2))

    with _FieldInsertCounter(db) as counter:
        updated = manager.update_embed(
            user_id=user.id, embed_id=embed.id, fields=_fields(6)
        )

    assert counter.single == 0, (
        f"update_embed issued {counter.single} per-field INSERTs; "
        "expected a single execute_many batch"
    )
    assert counter.batch == 1
    assert len(updated.fields) == 6


def test_batched_fields_preserve_order_and_values(db, auth_manager):
    """The batched rows must match the old per-field inserts exactly."""
    manager, user = _new_manager(db, auth_manager)
    fields = _fields(9)

    embed = manager.create_embed(user_id=user.id, title="order", fields=fields)

    assert [f.name for f in embed.fields] == [f["name"] for f in fields]
    assert [f.value for f in embed.fields] == [f["value"] for f in fields]
    assert [f.inline for f in embed.fields] == [f["inline"] for f in fields]

    new_fields = _fields(4)
    updated = manager.update_embed(
        user_id=user.id, embed_id=embed.id, fields=new_fields
    )
    assert [f.name for f in updated.fields] == [f["name"] for f in new_fields]
    assert [f.value for f in updated.fields] == [f["value"] for f in new_fields]
    assert [f.inline for f in updated.fields] == [f["inline"] for f in new_fields]
