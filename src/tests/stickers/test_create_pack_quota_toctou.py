"""TOCTOU regression for the sticker *pack* quotas in ``create_pack``.

``create_pack`` used to run a ``SELECT COUNT(*)`` quota check and then issue a
bare ``INSERT``. Two concurrent creators could both read a below-cap count and
both insert, pushing the server/user past ``max_packs_per_server`` /
``max_packs_per_user`` — the same quota-bypass class fixed for custom emoji,
reactions, per-pack stickers and per-server sounds. The cap is now enforced by
a single atomic guarded INSERT (plus a PostgreSQL advisory xact lock). These
tests prove the authoritative guard rejects the overflow even when the advisory
count read is stale (the losing-racer interleaving) and ratchet the source so
the guarded INSERT cannot silently regress to a bare INSERT.
"""

import ast
from pathlib import Path

import pytest

from src.core.stickers.exceptions import PackLimitError
from src.core.stickers.models import PackType

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/stickers/manager.py"
_SERVER_COUNT_SQL = "COUNT(*) as count FROM sticker_packs WHERE server_id"
_PERSONAL_COUNT_SQL = "COUNT(*) as count FROM sticker_packs "


def _count_server(db, server_id):
    return db.fetch_one(
        "SELECT COUNT(*) AS c FROM sticker_packs WHERE server_id = ?", (server_id,)
    )["c"]


def _count_personal(db, user_id):
    return db.fetch_one(
        "SELECT COUNT(*) AS c FROM sticker_packs WHERE created_by = ?", (user_id,)
    )["c"]


def _seed_server(db):
    db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
        "VALUES (1, 'Test', 1, 1000, 1000)"
    )
    db.execute(
        "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) "
        "VALUES (1, 1, 1, 1000, 1000)"
    )


def test_server_pack_cap_holds_when_advisory_count_is_stale(
    sticker_manager, test_db, monkeypatch
):
    limit = 2
    monkeypatch.setitem(sticker_manager._config, "max_packs_per_server", limit)
    _seed_server(test_db)

    for index in range(limit):
        sticker_manager.create_pack(
            1, f"Pack{index}", server_id=1, pack_type=PackType.SERVER
        )
    assert _count_server(test_db, 1) == limit

    real_fetch_one = test_db.fetch_one

    def stale_count(sql, params=None):
        if isinstance(sql, str) and _SERVER_COUNT_SQL in sql:
            return {"count": 0}
        return real_fetch_one(sql, params)

    monkeypatch.setattr(test_db, "fetch_one", stale_count)

    with pytest.raises(PackLimitError):
        sticker_manager.create_pack(
            1, "Overflow", server_id=1, pack_type=PackType.SERVER
        )
    assert _count_server(test_db, 1) == limit


def test_personal_pack_cap_holds_when_advisory_count_is_stale(
    sticker_manager, test_db, monkeypatch
):
    limit = 2
    monkeypatch.setitem(sticker_manager._config, "max_packs_per_user", limit)

    for index in range(limit):
        sticker_manager.create_pack(1, f"Pack{index}", pack_type=PackType.PERSONAL)
    assert _count_personal(test_db, 1) == limit

    real_fetch_one = test_db.fetch_one

    def stale_count(sql, params=None):
        if isinstance(sql, str) and _PERSONAL_COUNT_SQL in sql:
            return {"count": 0}
        return real_fetch_one(sql, params)

    monkeypatch.setattr(test_db, "fetch_one", stale_count)

    with pytest.raises(PackLimitError):
        sticker_manager.create_pack(1, "Overflow", pack_type=PackType.PERSONAL)
    assert _count_personal(test_db, 1) == limit


def test_create_pack_uses_guarded_insert_source_invariant():
    """Ratchet: create_pack must keep the atomic guarded INSERTs + PG locks."""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    sql_constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name)
            and t.id
            in ("_ATOMIC_SERVER_PACK_INSERT_SQL", "_ATOMIC_PERSONAL_PACK_INSERT_SQL")
            for t in node.targets
        ):
            name = next(
                t.id
                for t in node.targets
                if isinstance(t, ast.Name) and t.id.startswith("_ATOMIC_")
            )
            sql_constants[name] = ast.literal_eval(node.value)

    assert set(sql_constants) == {
        "_ATOMIC_SERVER_PACK_INSERT_SQL",
        "_ATOMIC_PERSONAL_PACK_INSERT_SQL",
    }
    for name, sql in sql_constants.items():
        upper = sql.upper()
        assert "INSERT INTO STICKER_PACKS" in upper, name
        assert "SELECT" in upper, name
        assert "WHERE" in upper, name
        assert "COUNT(" in upper, f"quota guard subquery missing from {name}"

    create_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_pack"
    )
    rendered = ast.dump(create_fn)
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
    assert "_ATOMIC_SERVER_PACK_INSERT_SQL" in rendered
    assert "_ATOMIC_PERSONAL_PACK_INSERT_SQL" in rendered
