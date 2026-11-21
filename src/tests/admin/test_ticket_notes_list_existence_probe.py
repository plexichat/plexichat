"""The ticket notes LIST must check existence with a cheap probe.

``GET /admin/tickets/{id}/notes`` only needs to know that the ticket exists
before listing its notes.  It used to call ``admin.get_ticket`` for that, which
ran the full ``feedback``/``auth_users`` JOIN (including note ciphertext) and,
with ``encrypt_internal_notes`` enabled, decrypted a note that no
``NoteResponse`` ever serializes.  These tests pin the handler to the small
``SELECT 1`` probe and to zero wasted decrypts.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BIG = 9007199254740993  # past 2^53

_NOTES_QUERY = (
    "SELECT n.id, n.ticket_id, n.admin_id, u.username, n.content, n.created_at "
    "FROM admin_notes n LEFT JOIN auth_users u ON n.admin_id = u.id "
    "WHERE n.ticket_id = ? ORDER BY n.created_at DESC LIMIT ?"
)


class _CountingDB:
    """Records normalized `(kind, query)` for every DB call."""

    def __init__(self, *, ticket=None):
        self.calls = []
        self.ticket = ticket

    def fetch_one(self, query, params=None):
        self.calls.append(("one", " ".join(query.split())))
        return self.ticket

    def fetch_all(self, query, params=None):
        self.calls.append(("all", " ".join(query.split())))
        return []

    def execute(self, query, params=None):
        self.calls.append(("exec", " ".join(query.split())))

    @property
    def reads(self):
        return [q for kind, q in self.calls if kind in ("one", "all")]


def _ticket_row():
    """A ticket carrying an encrypted internal note."""
    return {
        "id": BIG,
        "user_id": 7,
        "username": "user",
        "content": "please help",
        "category": "bug",
        "rating": 5,
        "status": "open",
        "created_at": 1700000000000,
        "resolved_at": None,
        "resolved_by": None,
        "internal_notes": None,
        "internal_notes_encrypted": "ENC:1:ciphertext",
    }


def _wire(monkeypatch, db):
    import src.api as api
    import src.api.routes.admin.tickets as routes
    import src.core.admin as admin_core
    import src.utils.encryption as enc
    import utils.config as config

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            True if key == "encryption.encrypt_internal_notes" else default
        ),
    )
    decrypts = []
    monkeypatch.setattr(
        enc, "decrypt_data", lambda value: decrypts.append(value) or "decoded note"
    )
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(admin_core, "_db", db)
    monkeypatch.setattr(admin_core, "_setup_complete", True)
    monkeypatch.setattr(routes, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(routes, "require_admin_permission", lambda request, perm: 1)
    return routes, decrypts


def test_notes_list_uses_cheap_existence_probe(monkeypatch):
    db = _CountingDB(ticket=_ticket_row())
    routes, decrypts = _wire(monkeypatch, db)

    result = asyncio.run(routes.get_ticket_notes(BIG, SimpleNamespace()))

    assert result == []
    assert db.reads == ["SELECT 1 FROM feedback WHERE id = ?", _NOTES_QUERY], db.reads
    assert decrypts == [], "notes list decrypted a discarded internal note"


def test_notes_list_missing_ticket_404_skips_notes_query(monkeypatch):
    db = _CountingDB(ticket=None)
    routes, decrypts = _wire(monkeypatch, db)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_ticket_notes(BIG, SimpleNamespace()))

    assert exc.value.status_code == 404
    assert db.reads == ["SELECT 1 FROM feedback WHERE id = ?"], db.reads
    assert decrypts == []
