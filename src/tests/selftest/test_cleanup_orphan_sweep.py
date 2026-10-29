"""The self-test must not leave data for accounts that do not exist.

Account erasure now erases the servers, participant-less conversations and
uploaded media an account owned, but a deployment can still carry rows from a
release before that cascade, and a crash mid-purge leaves a partial one. The
self-test's pre-run cleanup sweeps that residue -- and only that residue, so a
live user's server and uploads are never in scope.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from src.core.selftest.services.cleanup import CleanupService
from src.utils import encryption
from src.utils.encryption import generate_snowflake_id

pytestmark = pytest.mark.integration

MISSING_USER = 999_999_999_999_999_999


def _register(auth_manager, pii_gen):
    unique = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"sweep_{unique}",
            email=pii_gen.email(),
            password="TestPass123!",
        )


def _orphan_server(db, name="Left behind"):
    server_id = generate_snowflake_id()
    now = 1_700_000_000_000
    db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (server_id, name, MISSING_USER, now, now),
    )
    conversation_id = generate_snowflake_id()
    db.execute(
        "INSERT INTO msg_conversations "
        "(id, conversation_type, created_at, updated_at) VALUES (?, 'channel', ?, ?)",
        (conversation_id, now, now),
    )
    channel_id = generate_snowflake_id()
    db.execute(
        "INSERT INTO srv_channels "
        "(id, server_id, name, channel_type, position, conversation_id, "
        " created_at, updated_at) "
        "VALUES (?, ?, 'general', 'text', 0, ?, ?, ?)",
        (channel_id, server_id, conversation_id, now, now),
    )
    return server_id, channel_id, conversation_id


def _orphan_conversation(db):
    conversation_id = generate_snowflake_id()
    db.execute(
        "INSERT INTO msg_conversations "
        "(id, conversation_type, name, created_at, updated_at) "
        "VALUES (?, 'notes', 'Personal Notes', ?, ?)",
        (conversation_id, 1_700_000_000_000, 1_700_000_000_000),
    )
    return conversation_id


def _orphan_upload(db, path="image/2026/10/orphan.png"):
    file_id = generate_snowflake_id()
    db.execute(
        "INSERT INTO media_files "
        "(id, filename, original_filename, content_type, size, media_type, "
        " storage_backend, storage_path, checksum, uploaded_by, uploaded_at) "
        "VALUES (?, 'orphan.png', 'orphan.png', 'image/png', 44, 'image', "
        " 's3', ?, 'deadbeef', ?, ?)",
        (file_id, path, MISSING_USER, 1_700_000_000_000),
    )
    return file_id, path


def test_sweep_removes_rows_whose_account_is_gone(db, monkeypatch):
    monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)
    server_id, channel_id, server_conversation = _orphan_server(db)
    orphan_conversation = _orphan_conversation(db)
    file_id, path = _orphan_upload(db)

    result = CleanupService(ctx=None).sweep_orphans(db)

    assert server_id in result["servers"]
    assert db.fetch_one("SELECT id FROM srv_servers WHERE id = ?", (server_id,)) is None
    assert (
        db.fetch_one("SELECT id FROM srv_channels WHERE id = ?", (channel_id,)) is None
    )
    assert (
        db.fetch_one(
            "SELECT id FROM msg_conversations WHERE id = ?", (server_conversation,)
        )
        is None
    )
    assert (
        db.fetch_one(
            "SELECT id FROM msg_conversations WHERE id = ?", (orphan_conversation,)
        )
        is None
    )
    assert orphan_conversation in result["conversations"]
    assert db.fetch_one("SELECT id FROM media_files WHERE id = ?", (file_id,)) is None
    assert path in result["media_unswept"]


def test_sweep_leaves_a_live_users_data_alone(db, auth_manager, pii_gen, monkeypatch):
    monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)
    user = _register(auth_manager, pii_gen)
    server_id = generate_snowflake_id()
    now = 1_700_000_000_000
    db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
        "VALUES (?, 'Live server', ?, ?, ?)",
        (server_id, int(user.id), now, now),
    )
    file_id = generate_snowflake_id()
    db.execute(
        "INSERT INTO media_files "
        "(id, filename, original_filename, content_type, size, media_type, "
        " storage_backend, storage_path, checksum, uploaded_by, uploaded_at) "
        "VALUES (?, 'live.png', 'live.png', 'image/png', 44, 'image', 's3', "
        " 'image/live.png', 'cafe', ?, ?)",
        (file_id, int(user.id), now),
    )

    result = CleanupService(ctx=None).sweep_orphans(db)

    assert result["servers"] == []
    assert db.fetch_one("SELECT id FROM srv_servers WHERE id = ?", (server_id,))
    assert db.fetch_one("SELECT id FROM media_files WHERE id = ?", (file_id,))


def test_delete_all_for_user_erases_uploads_too(db, auth_manager, pii_gen, monkeypatch):
    monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)
    user = _register(auth_manager, pii_gen)
    file_id = generate_snowflake_id()
    db.execute(
        "INSERT INTO media_files "
        "(id, filename, original_filename, content_type, size, media_type, "
        " storage_backend, storage_path, checksum, uploaded_by, uploaded_at) "
        "VALUES (?, 'gone.png', 'gone.png', 'image/png', 44, 'image', 's3', "
        " 'image/gone.png', 'feed', ?, ?)",
        (file_id, int(user.id), 1_700_000_000_000),
    )

    CleanupService(ctx=None).delete_all_for_user(db, int(user.id), purge_sum=False)

    assert db.fetch_one("SELECT id FROM media_files WHERE id = ?", (file_id,)) is None
    assert (
        db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (int(user.id),)) is None
    )
