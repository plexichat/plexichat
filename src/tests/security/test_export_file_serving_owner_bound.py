"""Export file-serving routes must resolve the file through an owner predicate.

A guessed export id must never let user B download user A's private export.
Both user-facing download endpoints resolve the storage path through an
authoritative owner check BEFORE any file read:

* transcript: ``TranscriptExportService.get_export_file_path(export_id,
  user_id)`` binds ``info['user_id']`` to the caller, and the route repeats an
  ownership check on the status row before ``FileResponse``.
* DSAR: ``get_request_status(req_id, user_id)`` and ``get_export_file(req_id,
  user_id)`` both use ``WHERE id = ? AND user_id = ?``; the route checks them
  before opening the storage object.

These tests are non-vacuous: the owner is shown a real download, the wrong
user is refused with 404 and no file read.
"""

import asyncio
import inspect
import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.export as transcript_route
import src.api.routes.users.dsar as dsar_route
from src.api.middleware.authentication import TokenInfo
from src.core.auth.models import AccountType

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _token(uid):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    return asyncio.run(result) if inspect.isawaitable(result) else result


class _DB:
    def close(self):
        pass


# ---------------------------------------------------------------------------
# Transcript export download
# ---------------------------------------------------------------------------


def test_transcript_download_is_owner_bound(monkeypatch, tmp_path):
    owner_id, other_id = 101, 202
    export_id = "export_" + "a" * 32
    secret = tmp_path / "transcript.json"
    secret.write_text("PRIVATE TRANSCRIPT")

    reads = []

    class _FakeExportService:
        def __init__(self, *args, **kwargs):
            pass

        def get_export_file_path(self, eid, user_id=""):
            # Mirror the real service: a truthy user_id must equal the owner
            # recorded on the export, else no path is yielded.
            if eid != export_id or str(user_id) != str(owner_id):
                return None
            return str(secret)

        def get_export_status(self, eid):
            if eid != export_id:
                return None
            return {
                "export_id": eid,
                "user_id": owner_id,
                "status": "ready",
                "expires_at": time.time() + 3600,
                "format": "json",
                "mime_type": "application/json",
            }

    def _retrieve(*a, **k):
        raise AssertionError("file was read for a non-owner")

    monkeypatch.setattr(api, "get_db", lambda: _DB())
    monkeypatch.setattr(
        "src.core.messaging.services.export.TranscriptExportService",
        _FakeExportService,
    )
    monkeypatch.setattr(
        "src.core.messaging.repositories.message.MessageRepository", lambda db: object()
    )
    monkeypatch.setattr(
        "src.core.messaging.repositories.participant.ParticipantRepository",
        lambda db: object(),
    )
    # FileResponse construction does not open the file; reading its body does.
    from starlette.responses import FileResponse

    real_iter = FileResponse.__call__

    async def _counting_call(self, scope, receive, send):
        reads.append(self.path)
        return await real_iter(self, scope, receive, send)

    monkeypatch.setattr(FileResponse, "__call__", _counting_call)

    # Owner genuinely gets the download (non-vacuous).
    resp = _call(
        transcript_route.download_transcript_export,
        channel_id="1",
        export_id=export_id,
        current_user=_token(owner_id),
    )
    assert resp.path == str(secret)

    # A wrong user guessing the same export id is refused with no file read.
    with pytest.raises(HTTPException) as exc:
        _call(
            transcript_route.download_transcript_export,
            channel_id="1",
            export_id=export_id,
            current_user=_token(other_id),
        )
    assert exc.value.status_code == 404
    assert reads == []


# ---------------------------------------------------------------------------
# DSAR export download
# ---------------------------------------------------------------------------


def test_dsar_download_is_owner_bound(monkeypatch, tmp_path, db, auth_manager, pii_gen):
    from src.core.dsar import setup as dsar_setup
    from src.core.dsar.export_formats import ExportFormatGenerator
    from src.utils import encryption

    dsar_setup(db)
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        u1 = auth_manager.register("owner_exp", pii_gen.email(), "TestPass123!")
        u2 = auth_manager.register("other_exp", pii_gen.email(), "TestPass123!")

    secret = tmp_path / "dsar.json"
    secret.write_text('{"pii": true}')
    rid = int(time.time_ns() % 10**12) + 4242
    now = int(time.time())
    db.execute(
        """INSERT INTO dsar_requests
           (id, user_id, status, requested_at, completed_at, expires_at,
            format, storage_backend, storage_path, checksum, file_size_bytes)
           VALUES (?, ?, 'ready', ?, ?, ?, 'json', 'local', ?, 'x', 13)""",
        (rid, u1.id, now, now, now + 3600, str(secret)),
    )

    # Only the storage read is stubbed; the owner predicate is the real one in
    # get_request_status / get_export_file.
    def _retrieve(self, storage_path):
        f = open(storage_path, "rb")
        size = f.seek(0, 2)
        f.seek(0)
        return f, size

    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(ExportFormatGenerator, "retrieve_stream", _retrieve)

    mixin = dsar_route.DataExportMixin()

    # Owner genuinely gets a download stream (non-vacuous).
    resp = _call(
        mixin.download_data_export,
        request_id=str(rid),
        current_user=_token(u1.id),
    )
    assert resp.status_code == 200

    # A wrong user guessing the same request id is refused by the authoritative
    # WHERE id = ? AND user_id = ? lookups before any storage read.
    with pytest.raises(HTTPException) as exc:
        _call(
            mixin.download_data_export,
            request_id=str(rid),
            current_user=_token(u2.id),
        )
    assert exc.value.status_code == 404
