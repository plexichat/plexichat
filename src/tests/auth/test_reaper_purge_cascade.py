"""Regression: erasing an account must erase what the account owned.

``purge_user`` deleted the ``auth_users`` row and scrubbed a few domains, but
a user's servers (with their channels, conversations and messages), the
conversations nobody else took part in, and uploaded media all kept their own
rows. After a purge they were owned by an id that no longer existed: nobody
could administer or erase them, and the personal data stayed on the server for
ever. ``purge_user`` now runs ``src.core.erasure`` before dropping the account.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

from src.core.auth.deletion_log import DeletionLog
from src.core.auth.reaper import AccountReaper
from src.utils import encryption

pytestmark = pytest.mark.integration


def _register(auth_manager, pii_gen, label="cascade"):
    unique = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"{label}_{unique}",
            email=pii_gen.email(),
            password="TestPass123!",
        )


@pytest.fixture
def env(db, auth_manager, pii_gen, tmp_path):
    user = _register(auth_manager, pii_gen, "cascade")
    survivor = _register(auth_manager, pii_gen, "cascadekeep")
    reaper = AccountReaper(db, auth_manager)
    # Anonymise rather than delete messages, which is the shipped default; the
    # copy is taken so the shared config dict is not mutated for other tests.
    reaper._config = dict(reaper._config)
    reaper._config["anonymize_content"] = True
    log_path = tmp_path / "deletion_log.jsonl"
    reaper._log = DeletionLog(file_path=str(log_path))
    return db, auth_manager, user, survivor, reaper, log_path


def _purged_metadata(log_path):
    entries = [
        json.loads(line) for line in log_path.read_text().splitlines() if line.strip()
    ]
    purged = [e for e in entries if e.get("action") == "PURGED"]
    assert purged, "expected a PURGED event in the deletion log"
    return purged[-1].get("metadata") or {}


def _server_channels(db, server_id):
    return [
        dict(row)
        for row in db.fetch_all(
            "SELECT id, conversation_id FROM srv_channels WHERE server_id = ?",
            (int(server_id),),
        )
    ]


def _solo_conversation(db, user_id, body="notes to self"):
    """A conversation only this user takes part in (personal notes, a self-DM)."""
    from src.utils.encryption import generate_snowflake_id

    conversation_id = generate_snowflake_id()
    message_id = generate_snowflake_id()
    now = 1_700_000_000_000
    db.execute(
        "INSERT INTO msg_conversations "
        "(id, conversation_type, name, owner_id, created_at, updated_at) "
        "VALUES (?, 'notes', 'Personal Notes', ?, ?, ?)",
        (conversation_id, int(user_id), now, now),
    )
    db.execute(
        "INSERT INTO msg_participants (id, conversation_id, user_id, joined_at) "
        "VALUES (?, ?, ?, ?)",
        (generate_snowflake_id(), conversation_id, int(user_id), now),
    )
    db.execute(
        "INSERT INTO msg_messages "
        "(id, conversation_id, author_id, content, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (message_id, conversation_id, int(user_id), body, now, now),
    )
    return conversation_id, message_id


class TestOwnedServers:
    def test_purge_erases_the_server_the_account_owned(self, env, server_manager):
        db, _auth, user, _survivor, reaper, _ = env
        server = server_manager.create_server(int(user.id), "Erased with its owner")
        server_id = int(server.id)
        channels = _server_channels(db, server_id)
        assert channels, "expected the new server to have its default channel"

        reaper.purge_user(user.id, user.username)

        assert (
            db.fetch_one("SELECT id FROM srv_servers WHERE id = ?", (server_id,))
            is None
        )
        assert (
            db.fetch_one(
                "SELECT id FROM srv_channels WHERE server_id = ?", (server_id,)
            )
            is None
        )
        for channel in channels:
            conversation_id = channel.get("conversation_id")
            if conversation_id is None:
                continue
            assert (
                db.fetch_one(
                    "SELECT id FROM msg_conversations WHERE id = ?",
                    (int(conversation_id),),
                )
                is None
            )

    def test_a_server_owned_by_someone_else_survives(self, env, server_manager):
        db, _auth, user, survivor, reaper, _ = env
        kept = server_manager.create_server(int(survivor.id), "Not mine to erase")
        kept_id = int(kept.id)

        reaper.purge_user(user.id, user.username)

        assert (
            db.fetch_one("SELECT id FROM srv_servers WHERE id = ?", (kept_id,))
            is not None
        )
        assert db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ?", (kept_id,)
        )


class TestConversations:
    def test_conversation_left_without_participants_is_erased(self, env):
        db, _auth, user, _survivor, reaper, _ = env
        conversation_id, message_id = _solo_conversation(db, user.id)

        reaper.purge_user(user.id, user.username)

        assert (
            db.fetch_one(
                "SELECT id FROM msg_conversations WHERE id = ?", (conversation_id,)
            )
            is None
        )
        assert (
            db.fetch_one("SELECT id FROM msg_messages WHERE id = ?", (message_id,))
            is None
        )
        assert (
            db.fetch_one(
                "SELECT id FROM msg_participants WHERE conversation_id = ?",
                (conversation_id,),
            )
            is None
        )

    def test_dm_with_a_surviving_participant_is_kept_anonymised(
        self, env, messaging_manager
    ):
        db, _auth, user, survivor, reaper, _ = env
        conversation = messaging_manager.create_dm(int(user.id), int(survivor.id))
        conversation_id = int(conversation.id)
        message = messaging_manager.send_message(
            int(user.id), conversation_id, "the erased user's line"
        )
        message_id = int(message.id)

        reaper.purge_user(user.id, user.username)

        assert (
            db.fetch_one(
                "SELECT id FROM msg_conversations WHERE id = ?", (conversation_id,)
            )
            is not None
        ), "the other participant's copy of the DM must survive"
        assert (
            db.fetch_one(
                "SELECT id FROM msg_participants WHERE conversation_id = ? "
                "AND user_id = ?",
                (conversation_id, int(user.id)),
            )
            is None
        )
        # The erased user's own line goes with the account: anonymised when the
        # deployment keeps message bodies, removed by the author FK otherwise.
        assert (
            db.fetch_one(
                "SELECT id FROM msg_messages WHERE author_id = ?", (int(user.id),)
            )
            is None
        )
        assert (
            db.fetch_one("SELECT id FROM msg_messages WHERE id = ?", (message_id,))
            is None
        )


class TestMedia:
    def _insert_upload(self, db, user_id, *, checksum="abc123", path="image/x.png"):
        from src.utils.encryption import generate_snowflake_id

        file_id = generate_snowflake_id()
        db.execute(
            "INSERT INTO media_files "
            "(id, filename, original_filename, content_type, size, media_type, "
            " storage_backend, storage_path, checksum, uploaded_by, uploaded_at) "
            "VALUES (?, ?, ?, 'image/png', 44, 'image', 's3', ?, ?, ?, ?)",
            (
                file_id,
                path.rsplit("/", 1)[-1],
                path.rsplit("/", 1)[-1],
                path,
                checksum,
                int(user_id),
                1_700_000_000_000,
            ),
        )
        return file_id, path

    def test_uploads_are_erased_through_the_media_domain(self, env, monkeypatch):
        db, _auth, user, _survivor, reaper, _ = env
        file_id, _path = self._insert_upload(db, user.id)
        calls = []

        class FakeMedia:
            def delete_files_for_user(self, user_id):
                calls.append(int(user_id))
                db.execute(
                    "DELETE FROM media_files WHERE uploaded_by = ?", (int(user_id),)
                )
                return []

        monkeypatch.setattr("src.core.erasure._media_manager", lambda: FakeMedia())

        reaper.purge_user(user.id, user.username)

        assert calls == [int(user.id)]
        assert (
            db.fetch_one("SELECT id FROM media_files WHERE id = ?", (file_id,)) is None
        )

    def test_without_the_media_domain_the_row_goes_and_the_object_is_reported(
        self, env, monkeypatch
    ):
        db, _auth, user, _survivor, reaper, log_path = env
        file_id, path = self._insert_upload(db, user.id, path="image/2026/10/gone.png")
        monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)

        reaper.purge_user(user.id, user.username)

        assert (
            db.fetch_one("SELECT id FROM media_files WHERE id = ?", (file_id,)) is None
        )
        metadata = _purged_metadata(log_path)
        assert metadata["media_objects_unswept"] == 1
        assert metadata["media_object_paths"] == [path]

    def test_another_users_uploads_are_untouched(self, env, monkeypatch):
        db, _auth, user, survivor, reaper, _ = env
        kept_id, _path = self._insert_upload(db, survivor.id, checksum="keep")
        monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)

        reaper.purge_user(user.id, user.username)

        assert db.fetch_one("SELECT id FROM media_files WHERE id = ?", (kept_id,))
