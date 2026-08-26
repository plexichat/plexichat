"""Regression tests for admin note confidentiality and correctness."""

from unittest.mock import MagicMock


def test_get_user_notes_supports_tuple_rows_and_decrypts(monkeypatch):
    from src.core.admin import users

    db = MagicMock()
    db.fetch_one.return_value = ("legacy plaintext", "ciphertext")
    monkeypatch.setattr(
        "utils.config.get",
        lambda key, default=None: key == "encryption.encrypt_internal_notes",
    )
    monkeypatch.setattr(
        "src.utils.encryption.decrypt_data", lambda value: "decoded note"
    )

    assert users.get_user_notes(db, 7) == "decoded note"


def test_get_user_notes_fails_closed_on_decryption_error(monkeypatch):
    from src.core.admin import users

    db = MagicMock()
    db.fetch_one.return_value = ("legacy plaintext", "broken ciphertext")
    monkeypatch.setattr(
        "utils.config.get",
        lambda key, default=None: key == "encryption.encrypt_internal_notes",
    )

    def fail_decrypt(value):
        raise ValueError("bad authentication tag")

    monkeypatch.setattr("src.utils.encryption.decrypt_data", fail_decrypt)

    assert users.get_user_notes(db, 7) == ""


def test_save_user_notes_does_not_store_plaintext_when_encrypted(monkeypatch):
    from src.core.admin import users

    db = MagicMock()
    monkeypatch.setattr(
        "utils.config.get",
        lambda key, default=None: key == "encryption.encrypt_internal_notes",
    )
    monkeypatch.setattr("src.utils.encryption.encrypt_data", lambda value: "ciphertext")

    assert users.save_user_notes(db, 7, "secret note", 11) is True
    query, params = db.execute.call_args.args
    assert "internal_notes = NULL" in query
    assert params == ("ciphertext", 7)
    assert "secret note" not in params


def test_ticket_note_update_does_not_store_plaintext_when_encrypted(monkeypatch):
    from src.core.admin import tickets

    db = MagicMock()
    monkeypatch.setattr(
        "utils.config.get",
        lambda key, default=None: key == "encryption.encrypt_internal_notes",
    )
    monkeypatch.setattr("src.utils.encryption.encrypt_data", lambda value: "ciphertext")

    assert tickets.update_ticket_internal_notes(db, 3, "secret ticket note", 11) is True
    query, params = db.execute.call_args.args
    assert "internal_notes = NULL" in query
    assert params == ("ciphertext", 3)
    assert "secret ticket note" not in params


def test_markdown_notes_are_sanitized():
    from src.core.admin.notes import AdminNotesManager

    rendered = AdminNotesManager(MagicMock()).render_markdown(
        "<script>alert(1)</script>\n\n**safe** <img src=x onerror=alert(1)>"
    )

    assert "<script" not in rendered.lower()
    assert "onerror" not in rendered.lower()
    assert "<strong>safe</strong>" in rendered


def test_version_allocator_retries_unique_collision(monkeypatch):
    from src.core.admin.notes import AdminNotesManager

    db = MagicMock()
    db.fetch_one.side_effect = [{"max_version": 0}, {"max_version": 1}]
    db.execute.side_effect = [RuntimeError("UNIQUE constraint failed"), MagicMock()]
    manager = AdminNotesManager(db)

    version = manager._insert_version(1, "user", 7, "content", "plain", 11, 1000, None)

    assert version == 2
    assert db.rollback.called
    assert db.execute.call_count == 2
