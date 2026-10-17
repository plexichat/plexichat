"""
Suite registry route tests.

Covers the registry-level fixes that were applied alongside the app manifest
and shared-feed work:

- ``_activity_summary`` renders audit ``details_json`` as a short, human-
  readable string instead of leaking raw JSON into the activity feed.
- The shared feed no longer over-fetches the caller's personal content
  (scope filtering is exercised at the manager level in
  ``test_sum_search.py``; here we pin the registry's own summary contract).
"""

import asyncio
import types

from src.api.routes.suite.registry import _activity_summary


def test_activity_summary_returns_none_for_empty_or_garbage():
    assert _activity_summary(None, "pin", "workbook") is None
    assert _activity_summary("", "pin", "workbook") is None
    assert _activity_summary("not-json", "pin", "workbook") is None
    # A JSON list is not a details dict.
    assert _activity_summary("[]", "pin", "workbook") is None
    # A dict with no recognised keys yields no summary.
    assert _activity_summary("{}", "pin", "workbook") is None


def test_suite_artifacts_filters_to_suite_types():
    """Chat artifacts (voice/upload/file/transcript) are excluded from feeds."""
    from src.core.artifacts.models import Artifact, ArtifactStatus, ArtifactType
    from src.api.routes.suite.registry import _suite_artifacts

    def make(artifact_type: str) -> Artifact:
        return Artifact(
            id=1,
            conversation_id=None,
            channel_id=None,
            server_id=None,
            author_id=1,
            artifact_type=ArtifactType(artifact_type),
            title="t",
            summary=None,
            status=ArtifactStatus.COMPLETED,
            recorded=False,
            has_transcript=False,
            payload={},
            created_at=0,
            updated_at=0,
        )

    artifacts = [
        make(t)
        for t in (
            "plexiscribe",
            "plexiscript",
            "whiteboard",
            "voice_call",
            "upload",
            "file",
            "transcript",
            "future",
        )
    ]
    kept = _suite_artifacts(artifacts)
    assert {a.artifact_type.value for a in kept} == {
        "plexiscribe",
        "plexiscript",
        "whiteboard",
    }


def test_activity_summary_renders_pinned():
    assert _activity_summary('{"pinned": true}', "pin", "workbook") == "pinned"


def test_activity_summary_renders_template():
    assert (
        _activity_summary('{"template": true}', "template", "workbook")
        == "set as template"
    )


def test_activity_summary_renders_share_with_permissions():
    out = _activity_summary(
        '{"subject_id": 5, "permissions": ["view", "edit"]}', "share", "item"
    )
    assert out == "with user 5, view, edit"


def test_activity_summary_renders_scope_and_version():
    assert _activity_summary('{"scope": "server"}', "create", "workbook") == "server"
    assert (
        _activity_summary('{"version_id": 9}', "create_version", "item") == "version 9"
    )


def test_activity_summary_never_returns_raw_json():
    """The whole point: the feed shows a summary, not a raw JSON blob."""
    raw = '{"pinned": true, "scope": "server"}'
    assert _activity_summary(raw, "pin", "workbook") == "pinned, server"
    assert "{" not in _activity_summary(raw, "pin", "workbook")


def test_list_suite_apps_does_not_evaluate_voice_capabilities(monkeypatch):
    """The app catalog must not pay for the voice-transcription evaluator.

    The full capability catalog imports the transcription provider on every
    request; the Suite catalog only needs its own five capabilities, so
    ``/suite/apps`` must evaluate each suite feature independently and never
    touch the voice evaluators.
    """
    import src.core.artifacts.capabilities as caps_mod
    from src.api.routes.suite import registry as registry_routes

    def boom(artifacts):
        raise AssertionError("voice evaluator must not run for the Suite catalog")

    monkeypatch.setattr(caps_mod, "_eval_voice_transcription", boom)
    monkeypatch.setattr(caps_mod, "_eval_voice_recording", boom)

    result = asyncio.run(
        registry_routes.list_suite_apps(current_user=types.SimpleNamespace(user_id=42))
    )
    app_ids = {app["id"] for app in result["apps"]}
    assert app_ids == {
        "plexichat",
        "plexiscribe",
        "plexiscript",
        "plexiboard",
        "plexistash",
        "plexisum",
    }
