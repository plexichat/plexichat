"""Regression coverage for the security helpers wired into the *live* export route.

The helper unit tests prove the primitives are safe in isolation. These tests go
through ``_export_artifact_impl`` itself, so removing the wiring from the route
makes them fail. This covers the hostile download filename (including a ``..``
traversal stem), the IDOR guard that binds the fetched record to the requested
id, and the export size budgets enforced before a response is streamed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from src.api.middleware.authentication import TokenInfo
import src.api.routes.artifacts.routes.exports as exports
from src.core.artifacts.export import security as export_security


class _Artifact:
    id = 123
    conversation_id = 456
    server_id = None
    author_id = 789
    title = "doc"
    summary = "sum"
    artifact_type = SimpleNamespace(value="plexiscript")
    payload = {"content": "x" * 200}


class _Manager:
    def __init__(self, artifact=None):
        self.artifact = artifact if artifact is not None else _Artifact()

    def get(self, aid):
        return self.artifact


def _patch(monkeypatch, *, artifact=None, authorize=None):
    monkeypatch.setattr(exports, "_get_manager", lambda: _Manager(artifact))
    monkeypatch.setattr(exports, "_require_artifact_capability", lambda *_: None)
    monkeypatch.setattr(
        exports,
        "_authorize_scope",
        authorize or (lambda *a, **k: None),
    )


def _call(artifact_id="123", export_format="txt", user_id=1):
    return asyncio.run(
        exports._export_artifact_impl(
            artifact_id,
            export_format=export_format,
            current_user=cast(TokenInfo, SimpleNamespace(user_id=user_id)),
        )
    )


async def _collect(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return chunks


@pytest.mark.parametrize(
    "hostile_title",
    [
        "../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "evil..final\r\nX-Injected: 1",
        "report..final",
        '\u202eexe..doc"',
    ],
)
def test_live_export_sanitizes_hostile_download_filename(monkeypatch, hostile_title):
    """A hostile/traversal title cannot steer the download filename or headers."""
    artifact = _Artifact()
    artifact.title = hostile_title
    _patch(monkeypatch, artifact=artifact)

    response = _call()
    assert isinstance(response, StreamingResponse)
    header = response.headers["content-disposition"]

    assert ".." not in header
    assert "/" not in header and "\\" not in header
    assert "\r" not in header and "\n" not in header
    assert header.count('"') == 2  # only the quoted ascii filename's delimiters
    assert "; X-" not in header
    assert header.startswith('attachment; filename="')
    assert "; filename*=UTF-8''" in header
    assert header.rsplit("filename=", 1)[1].endswith(".txt")

    # The legitimate payload is still delivered unchanged.
    assert b"".join(asyncio.run(_collect(response))) == b"x" * 200


def test_live_export_rejects_idor_when_record_id_does_not_match_request(monkeypatch):
    """The access guard must bind the returned artifact to the requested id."""
    mismatched = _Artifact()
    mismatched.id = 999  # repository handed back a different artifact
    _patch(monkeypatch, artifact=mismatched)

    with pytest.raises(HTTPException) as exc:
        _call(artifact_id="123")
    assert exc.value.status_code == 403


def test_live_export_denies_out_of_scope_reader(monkeypatch):
    def deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="denied")

    _patch(monkeypatch, authorize=deny)
    with pytest.raises(HTTPException) as exc:
        _call(user_id=1)
    assert exc.value.status_code == 403


def test_live_export_enforces_byte_budget_before_streaming(monkeypatch):
    monkeypatch.setattr(export_security, "MAX_EXPORT_BYTES", 64)
    _patch(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        _call()
    assert exc.value.status_code == 413


def test_live_export_enforces_text_budget_before_rendering(monkeypatch):
    monkeypatch.setattr(export_security, "MAX_EXPORT_CHARS", 64)
    _patch(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        _call()
    assert exc.value.status_code == 413
