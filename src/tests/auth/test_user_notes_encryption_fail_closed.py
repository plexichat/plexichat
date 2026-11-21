"""Regression tests: private user notes must never fall back to plaintext.

``UserNotesService`` used to swallow a failed ``encrypt_data()`` call and
persist the note verbatim via ``note_encrypted or note``. That is a fail-open
on the encryption-at-rest contract: exactly when the keyring is misconfigured,
a private note (which may hold sensitive moderation text) was written to the
database, its backups and any reader of the column in the clear.

The service now substitutes a non-reversible ``[unavailable]`` marker instead
of the plaintext, and a decryption failure no longer returns the raw stored
value as the note. These tests pin both boundaries and confirm that the
opt-out (``encryption_enabled=False``) is preserved.

Run with: pytest src/tests/auth/test_user_notes_encryption_fail_closed.py -q
"""

import pytest

from src.core.auth.services import user_notes as user_notes_module
from src.core.auth.services.user_notes import UserNotesService


AUTHOR = 11
TARGET = 22
SECRET = "sensitive moderation note - do not store in clear"


class _Repo:
    """In-memory stand-in for UserNotesRepository that records every write."""

    def __init__(self, existing=None):
        self.rows = {}
        self.creates = []
        self.updates = []
        self._next_id = 100
        for (user_id, target_id), blob in (existing or {}).items():
            self.rows[(user_id, target_id)] = {
                "id": self._next_id,
                "user_id": user_id,
                "target_user_id": target_id,
                "note_encrypted": blob,
                "created_at": 1,
                "updated_at": 1,
            }
            self._next_id += 1

    def exists(self, user_id, target_user_id):
        return (user_id, target_user_id) in self.rows

    def get(self, user_id, target_user_id):
        row = self.rows.get((user_id, target_user_id))
        return dict(row) if row else None

    def create(
        self, note_id, user_id, target_user_id, note_encrypted, created_at, updated_at
    ):
        row = {
            "id": note_id,
            "user_id": user_id,
            "target_user_id": target_user_id,
            "note_encrypted": note_encrypted,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        self.rows[(user_id, target_user_id)] = row
        self.creates.append(dict(row))

    def update(self, note_id, note_encrypted, updated_at):
        for row in self.rows.values():
            if row["id"] == note_id:
                row["note_encrypted"] = note_encrypted
                row["updated_at"] = updated_at
                self.updates.append(note_encrypted)
                return
        raise AssertionError(f"no row with id {note_id}")


def _service(repo, *, encryption_enabled=True):
    svc = UserNotesService(None, encryption_enabled=encryption_enabled)
    svc._repo = repo
    return svc


def _fail_decrypt(value):
    raise RuntimeError("keyring unavailable")


@pytest.mark.auth
class TestUserNotesEncryptionFailClosed:
    def test_create_never_persists_plaintext_when_encryption_fails(self, monkeypatch):
        def _fail_encrypt(value):
            raise RuntimeError("keyring unavailable")

        monkeypatch.setattr(user_notes_module, "encrypt_data", _fail_encrypt)
        monkeypatch.setattr(user_notes_module, "decrypt_data", _fail_decrypt)

        repo = _Repo()
        note = _service(repo).create_note(AUTHOR, TARGET, SECRET)

        stored = repo.rows[(AUTHOR, TARGET)]["note_encrypted"]
        assert SECRET not in stored
        assert stored == UserNotesService._ENCRYPTION_FAILURE_MARKER
        assert SECRET not in str(note)
        assert note["note"] == UserNotesService._ENCRYPTION_FAILURE_MARKER

    def test_update_never_persists_plaintext_when_encryption_fails(self, monkeypatch):
        repo = _Repo({(AUTHOR, TARGET): "FAKE-OLD-CIPHER"})
        old_id = repo.rows[(AUTHOR, TARGET)]["id"]

        def _fail_encrypt(value):
            raise RuntimeError("keyring unavailable")

        monkeypatch.setattr(user_notes_module, "encrypt_data", _fail_encrypt)
        monkeypatch.setattr(user_notes_module, "decrypt_data", _fail_decrypt)

        note = _service(repo).update_note(AUTHOR, TARGET, SECRET)

        stored = repo.rows[(AUTHOR, TARGET)]["note_encrypted"]
        assert SECRET not in stored
        assert stored == UserNotesService._ENCRYPTION_FAILURE_MARKER
        assert repo.rows[(AUTHOR, TARGET)]["id"] == old_id
        assert SECRET not in str(note)

    def test_decryption_failure_never_returns_raw_stored_value(self, monkeypatch):
        # A legacy plaintext row, or a redaction marker, must never be handed
        # back as if it were the decrypted note.
        legacy_blob = "legacy plaintext that must not be disclosed"
        repo = _Repo({(AUTHOR, TARGET): legacy_blob})

        monkeypatch.setattr(user_notes_module, "decrypt_data", _fail_decrypt)

        note = _service(repo).get_note(AUTHOR, TARGET)

        assert note["note"] != legacy_blob
        assert note["note"] == UserNotesService._ENCRYPTION_FAILURE_MARKER
        assert repo.rows[(AUTHOR, TARGET)]["note_encrypted"] == legacy_blob

    def test_encryption_disabled_still_stores_plaintext(self, monkeypatch):
        def _fail_if_called(value):
            raise AssertionError("encrypt_data must not be called when disabled")

        monkeypatch.setattr(user_notes_module, "encrypt_data", _fail_if_called)

        repo = _Repo()
        note = _service(repo, encryption_enabled=False).create_note(
            AUTHOR, TARGET, SECRET
        )

        assert repo.rows[(AUTHOR, TARGET)]["note_encrypted"] == SECRET
        assert note["note"] == SECRET
