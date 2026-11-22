"""Artifact event fan-out must fail closed when the audience cannot be resolved.

Artifact lifecycle / message events carry the artifact's title as message/event
content and derive their recipient list from the conversation's participant
roster. When that authoritative lookup fails, the emitters hand ``None`` to
``events.dispatch(user_ids=...)``. The EventRouter treats ``None`` as "route by
scope", and a server-scoped event is widened to *every* member of the server --
including members who never participated in the conversation. A lookup failure
therefore leaks the private artifact title to non-participants, the exact
fail-open shape the DM-mention fix (b62dad3b) closed for notifications.

An unverifiable audience must resolve to an empty list, never to ``None``.
"""

import src.api as api_mod
import src.core.events as events_mod
from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.models import Artifact, ArtifactStatus, ArtifactType

PRIVATE_TITLE = "private-conversation-artifact-title"


def _artifact():
    return Artifact(
        id=910001,
        conversation_id=920001,
        channel_id=930001,
        server_id=940001,
        author_id=1001,
        artifact_type=ArtifactType.FILE,
        title=PRIVATE_TITLE,
        summary=None,
        status=ArtifactStatus.LIVE,
        recorded=False,
        has_transcript=False,
        payload={},
        created_at=1,
        updated_at=1,
    )


class _FailingMessaging:
    def get_participant_ids(self, conversation_id):
        raise RuntimeError("participant store unavailable")


class _OkMessaging:
    def get_participant_ids(self, conversation_id):
        return [2001, 2002]


def _capture_dispatch(monkeypatch):
    captured = {}

    def fake_dispatch(event, **kwargs):
        captured["event"] = event
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(events_mod, "is_setup", lambda: True)
    monkeypatch.setattr(events_mod, "dispatch", fake_dispatch)
    return captured


def test_lifecycle_event_fails_closed_when_participant_lookup_raises(monkeypatch):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(api_mod, "get_messaging", lambda: _FailingMessaging())

    mgr = object.__new__(ArtifactManager)
    mgr._emit_lifecycle_event("create", _artifact())

    assert captured["user_ids"] == [], (
        "a failed participant lookup must yield an empty audience; None lets "
        "the router widen the private artifact title to every server member"
    )
    # Non-vacuity: the payload really does carry private content, and the
    # event really is server-scoped (the widening precondition).
    assert captured["event"].data["title"] == PRIVATE_TITLE
    assert captured["server_id"] == 940001


def test_lifecycle_event_fails_closed_when_messaging_unavailable(monkeypatch):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(api_mod, "get_messaging", lambda: None)

    mgr = object.__new__(ArtifactManager)
    mgr._emit_lifecycle_event("update", _artifact())

    assert captured["user_ids"] == [], (
        "an unwired messaging module must not widen a server-scoped artifact "
        "event carrying private content to the whole server"
    )


def test_lifecycle_event_keeps_genuine_participants(monkeypatch):
    """Positive control: a successful resolution is still honoured verbatim."""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(api_mod, "get_messaging", lambda: _OkMessaging())

    mgr = object.__new__(ArtifactManager)
    mgr._emit_lifecycle_event("delete", _artifact())

    assert captured["user_ids"] == [2001, 2002]


def test_artifact_message_event_fails_closed_when_participant_lookup_raises(monkeypatch):
    """The inline MESSAGE_CREATE carries the artifact title as message content."""
    from src.api.routes import artifacts_helpers
    from src.api.schemas.artifacts import ArtifactResponse

    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(artifacts_helpers, "_can_emit_artifact_message", lambda *a: True)

    class _Messaging(_FailingMessaging):
        def send_message(self, **kwargs):
            return object()

    monkeypatch.setattr(api_mod, "get_messaging", lambda: _Messaging())

    artifact = ArtifactResponse(
        id=910002,
        conversation_id=920001,
        channel_id=930001,
        server_id=940001,
        author_id=1001,
        artifact_type="file",
        title=PRIVATE_TITLE,
        status="live",
        created_at=1,
        updated_at=1,
    )
    artifacts_helpers._emit_artifact_message(artifact, {"id": 1001, "username": "a"})

    assert captured["user_ids"] == [], (
        "a failed participant lookup must not fall back to server-wide routing "
        "for the inline artifact message carrying the private title"
    )
    assert PRIVATE_TITLE in str(captured["event"].data.get("content"))


def test_transcription_update_event_fails_closed_when_lookup_raises(monkeypatch):
    from src.core.artifacts.transcription import worker as worker_mod

    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(api_mod, "get_messaging", lambda: _FailingMessaging())

    worker_mod._emit_artifact_update(_artifact(), 920001, 940001, 930001)

    assert captured["user_ids"] == [], (
        "transcription ARTIFACT_UPDATE must not be widened to the whole server "
        "when the participant audience cannot be resolved"
    )
