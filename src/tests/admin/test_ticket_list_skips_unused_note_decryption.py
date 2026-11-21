"""The admin ticket LIST must not decrypt internal notes it never serializes.

``GET /admin/tickets`` builds ``TicketResponse`` objects, which have no
``internal_notes`` field.  The core loader used to decrypt every ticket's
``internal_notes_encrypted`` value on the way out of the DB -- one AES-GCM
decrypt (and one deferred module import) per row, all of it thrown away by the
response schema.  These tests pin the counter at zero for the list path while
keeping the detail/default paths' decryption intact.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

N = 25


def _rows(n=N):
    """DB rows with an encrypted internal note on every ticket."""
    return [
        {
            "id": i + 1,
            "user_id": 900000000000000000 + i,
            "username": f"user{i}",
            "content": "please help",
            "category": "bug",
            "rating": 5,
            "status": "open",
            "created_at": 1700000000000,
            "resolved_at": None,
            "resolved_by": None,
            "internal_notes": None,
            "internal_notes_encrypted": f"ENC:1:ciphertext{i}",
        }
        for i in range(n)
    ]


def _encrypted_config(monkeypatch):
    import utils.config as config

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            True if key == "encryption.encrypt_internal_notes" else default
        ),
    )


def _count_decrypts(monkeypatch):
    import src.utils.encryption as enc

    calls = []
    monkeypatch.setattr(
        enc, "decrypt_data", lambda value: calls.append(value) or "decoded note"
    )
    return calls


def test_ticket_list_route_skips_unused_note_decryption(monkeypatch):
    import src.api.routes.admin.tickets as route_mod
    import src.core.admin as admin_core

    _encrypted_config(monkeypatch)
    decrypt_calls = _count_decrypts(monkeypatch)

    db = MagicMock()
    db.fetch_all.return_value = _rows()
    monkeypatch.setattr(admin_core, "_db", db)
    monkeypatch.setattr(admin_core, "_setup_complete", True)
    monkeypatch.setattr(route_mod, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(route_mod, "require_admin_permission", lambda r, p: 1)

    result = asyncio.run(
        route_mod.get_tickets(SimpleNamespace(), status_filter=None, limit=50, offset=0)
    )

    assert len(result) == N
    assert "internal_notes" not in result[0].model_dump()
    assert decrypt_calls == [], (
        f"ticket list decrypted {len(decrypt_calls)} notes it never serializes"
    )


def test_ticket_list_loader_opt_out_skips_decryption(monkeypatch):
    from src.core.admin import tickets

    _encrypted_config(monkeypatch)
    decrypt_calls = _count_decrypts(monkeypatch)

    db = MagicMock()
    db.fetch_all.return_value = _rows()

    rows = tickets.get_feedback_tickets(db, include_internal_notes=False)

    assert len(rows) == N
    assert all(row.internal_notes is None for row in rows)
    assert decrypt_calls == []


def test_default_loader_still_decrypts_notes(monkeypatch):
    """The public core API keeps its old behavior unless explicitly opted out."""
    from src.core.admin import tickets

    _encrypted_config(monkeypatch)
    decrypt_calls = _count_decrypts(monkeypatch)

    db = MagicMock()
    db.fetch_all.return_value = _rows()

    rows = tickets.get_feedback_tickets(db)

    assert len(rows) == N
    assert len(decrypt_calls) == N
    assert rows[0].internal_notes == "decoded note"
