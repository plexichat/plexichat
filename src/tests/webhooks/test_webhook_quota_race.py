"""TOCTOU regression for the webhook creation quotas.

``create_webhook`` used to run a ``SELECT COUNT(*)`` check for each of the
per-channel and per-server caps and *then* issue a separate bare ``INSERT``.
Two concurrent creators could both read a below-cap count before either row
committed and both insert, pushing a channel or server past its webhook quota.

The caps are now enforced by a single atomic guarded ``INSERT`` (plus a
PostgreSQL advisory lock). These tests prove the authoritative guard still
rejects the overflow when the advisory count read is stale (the losing-racer
interleaving that used to defeat the early checks) and ratchet the source so the
guarded INSERT cannot silently regress to a bare INSERT.
"""

import ast
from pathlib import Path

import pytest

from src.core.webhooks.exceptions import WebhookLimitError

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/webhooks/manager/crud.py"


def _channel_count(db, channel_id):
    return db.fetch_one(
        "SELECT COUNT(*) AS c FROM webhook_webhooks WHERE channel_id = ?",
        (channel_id,),
    )["c"]


def _server_count(db, server_id):
    return db.fetch_one(
        "SELECT COUNT(*) AS c FROM webhook_webhooks WHERE server_id = ?",
        (server_id,),
    )["c"]


def _stale_counts(mgr, monkeypatch):
    """Force the advisory count reads to report a stale below-cap value.

    This is the losing-racer interleaving: both requests read the pre-insert
    count before either webhook commits, so both early checks pass. Only the
    atomic guarded INSERT can still reject the overflow.
    """
    monkeypatch.setattr(mgr, "_get_channel_webhook_count", lambda cid: 0)
    monkeypatch.setattr(mgr, "_get_server_webhook_count", lambda sid: 0)


def test_channel_cap_enforced_when_advisory_count_stale(fresh_server, monkeypatch):
    setup = fresh_server
    mgr = setup["webhooks"]
    limit = 2
    monkeypatch.setitem(mgr._config, "max_webhooks_per_channel", limit)
    monkeypatch.setitem(mgr._config, "max_webhooks_per_server", 50)
    channel_id = setup["channel"].id
    owner_id = setup["owner"].id

    for index in range(limit):
        mgr.create_webhook(owner_id, channel_id, f"seed_{index}")
    assert _channel_count(setup["db"], channel_id) == limit

    _stale_counts(mgr, monkeypatch)

    with pytest.raises(WebhookLimitError):
        mgr.create_webhook(owner_id, channel_id, "overflow")

    assert _channel_count(setup["db"], channel_id) == limit


def test_server_cap_enforced_when_advisory_count_stale(fresh_server, monkeypatch):
    setup = fresh_server
    mgr = setup["webhooks"]
    from src.core.servers import ChannelType

    limit = 2
    monkeypatch.setitem(mgr._config, "max_webhooks_per_channel", 50)
    monkeypatch.setitem(mgr._config, "max_webhooks_per_server", limit)
    channel2 = setup["servers"].create_channel(
        user_id=setup["owner"].id,
        server_id=setup["server"].id,
        name="second-channel",
        channel_type=ChannelType.TEXT,
    )
    owner_id = setup["owner"].id

    # Two webhooks, each in its own channel: per-channel counts stay at 1 while
    # the per-server count is already at the cap.
    mgr.create_webhook(owner_id, setup["channel"].id, "seed_0")
    mgr.create_webhook(owner_id, channel2.id, "seed_1")
    assert _server_count(setup["db"], setup["server"].id) == limit

    _stale_counts(mgr, monkeypatch)

    with pytest.raises(WebhookLimitError):
        mgr.create_webhook(owner_id, channel2.id, "overflow")

    assert _server_count(setup["db"], setup["server"].id) == limit


def test_create_webhook_uses_guarded_insert_source_invariant():
    """Ratchet: create_webhook must keep the atomic guarded INSERT.

    A plain ``INSERT ... VALUES`` after separate COUNT checks reopens the
    TOCTOU. Require the guarded ``INSERT ... SELECT ... WHERE (SELECT COUNT)``
    statement and the PostgreSQL advisory-lock serialization.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    atomic = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ATOMIC_WEBHOOK_INSERT_SQL"
            for t in node.targets
        ):
            atomic = node.value
    assert atomic is not None, "atomic guarded INSERT statement missing"

    sql = ast.literal_eval(atomic)
    upper = sql.upper()
    assert "INSERT INTO WEBHOOK_WEBHOOKS" in upper
    assert "SELECT" in upper
    assert "WHERE" in upper
    assert "COUNT(" in upper, "quota guard subqueries missing from INSERT"

    create_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_webhook"
    )
    rendered = ast.dump(create_fn)
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
    assert "_ATOMIC_WEBHOOK_INSERT_SQL" in rendered, "guarded INSERT not used"
