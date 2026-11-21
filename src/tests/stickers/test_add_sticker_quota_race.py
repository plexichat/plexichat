"""TOCTOU regression for the per-pack custom-sticker quota.

``StickerManager.add_sticker`` used to run a ``SELECT COUNT(*)`` quota check and
*then* issue a separate bare ``INSERT``. Two concurrent creators could both read
a below-cap count before either committed and both insert, pushing the pack past
``max_stickers_per_pack`` — the same quota-bypass class already fixed for custom
emoji with an atomic guarded INSERT.

The cap is now enforced by a single atomic guarded INSERT (plus a PostgreSQL
advisory lock). These tests prove that the authoritative guard still rejects the
overflow even when the advisory count read is stale (the losing-racer
interleaving), and ratchet the source so the guarded INSERT cannot silently
regress to a bare INSERT.
"""

import ast
from pathlib import Path

import pytest

from src.core.stickers.exceptions import StickerLimitError
from src.core.stickers.models import PackType, StickerFormat

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/stickers/manager.py"
_COUNT_SQL = "COUNT(*) as count FROM sticker_stickers WHERE pack_id"


def _count(sticker_manager, pack_id):
    return sticker_manager._db.fetch_one(
        "SELECT COUNT(*) AS c FROM sticker_stickers WHERE pack_id = ?", (pack_id,)
    )["c"]


def test_guarded_insert_enforces_cap_when_advisory_count_is_stale(
    sticker_manager, two_users, monkeypatch
):
    """A stale below-cap count read cannot push the pack over its quota.

    The old code's only guard was the advisory ``SELECT COUNT(*)``. Simulate the
    losing racer whose read happened before the winning creator committed: the
    count query always returns 0. With the authoritative atomic INSERT guard in
    place, the overflow row is still refused and the cap holds exactly.
    """
    owner, _other = two_users
    limit = 3
    monkeypatch.setitem(sticker_manager._config, "max_stickers_per_pack", limit)

    pack = sticker_manager.create_pack(
        owner.id, "Quota Pack", pack_type=PackType.PERSONAL
    )
    for index in range(limit):
        sticker_manager.add_sticker(
            owner.id,
            pack.id,
            f"seed_{index}",
            StickerFormat.PNG,
            "https://x/seed.png",
            10,
        )
    assert _count(sticker_manager, pack.id) == limit

    # The advisory count read is stale, exactly as for the losing concurrent
    # racer. Only the authoritative guarded INSERT can now reject the row.
    real_fetch_one = sticker_manager._db.fetch_one

    def stale_count(sql, params=()):
        if _COUNT_SQL in sql:
            return {"count": 0}
        return real_fetch_one(sql, params)

    monkeypatch.setattr(sticker_manager._db, "fetch_one", stale_count)

    with pytest.raises(StickerLimitError):
        sticker_manager.add_sticker(
            owner.id,
            pack.id,
            "overflow",
            StickerFormat.PNG,
            "https://x/overflow.png",
            10,
        )

    assert _count(sticker_manager, pack.id) == limit


def test_add_sticker_uses_guarded_insert_source_invariant():
    """Ratchet: add_sticker must keep the atomic guarded INSERT.

    A plain ``INSERT ... VALUES`` after a separate COUNT check reopens the
    TOCTOU. Require the guarded ``INSERT ... SELECT ... WHERE (SELECT COUNT)``
    statement and the PostgreSQL advisory-lock serialization.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    atomic = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ATOMIC_STICKER_INSERT_SQL"
            for t in node.targets
        ):
            atomic = node.value
    assert atomic is not None, "atomic guarded INSERT statement missing"

    sql = ast.literal_eval(atomic)
    upper = sql.upper()
    assert "INSERT INTO STICKER_STICKERS" in upper
    assert "SELECT" in upper
    assert "WHERE" in upper
    assert "COUNT(" in upper, "quota guard subquery missing from INSERT"

    add_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "add_sticker"
    )
    rendered = ast.dump(add_fn)
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
    assert "_ATOMIC_STICKER_INSERT_SQL" in rendered, "guarded INSERT not used"
