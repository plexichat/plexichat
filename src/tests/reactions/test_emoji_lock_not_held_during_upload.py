"""Emoji quota advisory lock must not span the media upload.

Every other quota path (sticker/sound/webhook/role/event/invite) performs its
expensive work *before* taking the PostgreSQL advisory xact lock, then does only
the guarded INSERT under the lock. ``create_custom_emoji`` used to acquire the
per-(server, kind) lock and open its transaction before calling
``self._media.upload_file(...)``. That is storage/network I/O with no bearing on
the count guard, so a single slow upload serialised every other emoji creation
for the server behind it. The media pipeline also takes its own storage-quota
advisory lock inside that call, nesting it under the emoji lock.

The upload now runs before the lock/transaction. These tests prove no
transaction or advisory lock is held at upload time (behavioural, on the shared
SQLite fixture) and ratchet the source order so the regression cannot silently
return on PostgreSQL.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/reactions/manager/emojis.py"


def test_upload_runs_without_an_open_transaction(
    users_with_server, sample_image, monkeypatch
):
    """No DB transaction (hence no advisory lock) may be open at upload time.

    On the SQLite test backend ``begin_transaction`` is the same call that
    precedes the guarded INSERT on PostgreSQL; asserting it has not been made
    when ``upload_file`` runs proves the lock's critical section was shortened.
    On PostgreSQL the same source order holds the xact lock only after upload.
    """
    owner, _member, server, _group, _msg, _servers, reactions = users_with_server
    assert not reactions._db.in_transaction, "fixture must start outside a transaction"

    observed = []

    class FakeMedia:
        def upload_file(self, user_id, data, filename, content_type):
            observed.append(
                {
                    "in_transaction": bool(reactions._db.in_transaction),
                    "transaction_depth": getattr(reactions._db, "transaction_depth", 0),
                }
            )
            return SimpleNamespace(url="/media/emoji.png", metadata={})

    monkeypatch.setattr(reactions, "_media", FakeMedia())

    created = reactions.create_custom_emoji(
        owner.id, server.id, "lockscope", sample_image, "image/png"
    )
    assert created is not None
    assert observed, "media upload was never called"
    assert observed[0]["in_transaction"] is False, (
        "media upload ran while a DB transaction/advisory lock was held"
    )
    assert observed[0]["transaction_depth"] == 0, (
        "media upload ran inside a nested transaction"
    )


def test_source_ratchet_upload_precedes_lock_and_transaction():
    """Ratchet: in create_custom_emoji the upload precedes lock acquisition."""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    create_fn = next(
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_custom_emoji"
    )

    def _attr_chain(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    lines = {"upload": [], "begin": [], "advisory": []}
    for node in ast.walk(create_fn):
        if not isinstance(node, ast.Call):
            continue
        chain = _attr_chain(node.func)
        if chain == "self._media.upload_file":
            lines["upload"].append(node.lineno)
        elif chain == "self._db.begin_transaction":
            lines["begin"].append(node.lineno)
        elif any(
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and "pg_advisory_xact_lock" in arg.value
            for arg in node.args
        ):
            lines["advisory"].append(node.lineno)

    assert lines["upload"], "media upload call not found in create_custom_emoji"
    assert lines["begin"], "begin_transaction call not found"
    assert lines["advisory"], "pg_advisory_xact_lock call not found"
    assert max(lines["upload"]) < min(lines["begin"] + lines["advisory"]), (
        f"media upload must run before the advisory lock/transaction; "
        f"upload={lines['upload']} begin={lines['begin']} advisory={lines['advisory']}"
    )
