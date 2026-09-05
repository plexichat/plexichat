"""Tests for transcription recording-reference normalization."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.artifacts.transcription.provider import (
    AzureSpeechProvider,
    TranscriptionResult,
    azure_timespan_to_ms,
)
from src.core.artifacts.transcription import worker as transcription_worker
from src.core.artifacts.transcription.worker import (
    _merge_transcription_results,
    _resolve_recording_ref,
    _resolve_recording_segments,
)


def test_resolve_recording_ref_materializes_media_file_for_local_provider(tmp_path):
    media = Mock()
    media.get_file_data.return_value = (b"audio-bytes", "audio/webm")
    with patch("src.api.get_media", return_value=media):
        ref, temporary = _resolve_recording_ref(
            {
                "recording_ref": "/api/v1/media/attachments/recording.webm",
                "recording_file_id": 42,
            },
            db=Mock(),
            provider_name="local_whisper",
        )

    assert ref == temporary
    assert temporary is not None
    try:
        with open(temporary, "rb") as handle:
            assert handle.read() == b"audio-bytes"
        media.get_file_data.assert_called_once_with(42)
    finally:
        __import__("os").unlink(temporary)


def test_resolve_recording_ref_keeps_azure_url_unchanged():
    ref = "https://media.example.test/recording.webm"
    resolved, temporary = _resolve_recording_ref(
        {"recording_ref": ref, "recording_file_id": 42},
        db=Mock(),
        provider_name="azure",
    )
    assert resolved == ref
    assert temporary is None


def test_resolve_recording_ref_builds_public_azure_url_from_signed_media(
    monkeypatch,
):
    media = Mock()
    media.sign_url.return_value = Mock(
        url="/api/v1/media/attachments/recording.webm?expires=1&signature=sig"
    )
    monkeypatch.setenv("PLEXICHAT_PUBLIC_SERVER_URL", "https://chat.example.test")
    with patch("src.api.get_media", return_value=media):
        resolved, temporary = _resolve_recording_ref(
            {
                "recording_ref": "/api/v1/media/attachments/recording.webm",
                "recording_file_id": 42,
            },
            db=Mock(),
            provider_name="azure",
        )

    assert resolved == (
        "https://chat.example.test/api/v1/media/attachments/recording.webm"
        "?expires=1&signature=sig"
    )
    assert temporary is None
    media.sign_url.assert_called_once_with(42)


def test_resolve_recording_segments_materializes_every_multi_segment_file(
    tmp_path,
):
    """M27: every track of a consent-interrupted recording resolves, in order."""
    media = Mock()
    media.get_file_data.side_effect = [
        (b"segment-one-bytes", "audio/webm"),
        (b"segment-two-bytes", "audio/webm"),
    ]
    with patch("src.api.get_media", return_value=media):
        segments = _resolve_recording_segments(
            {
                "recording_ref": "/api/v1/media/attachments/call_5.webm",
                "recording_file_id": 11,
                "recording_file_ids": [11, 12],
            },
            db=Mock(),
            provider_name="local_whisper",
            call_id=5,
        )

    assert len(segments) == 2
    assert [ref for ref, _ in segments] == [tmp for _, tmp in segments]
    try:
        with open(segments[0][0], "rb") as handle:
            assert handle.read() == b"segment-one-bytes"
        with open(segments[1][0], "rb") as handle:
            assert handle.read() == b"segment-two-bytes"
    finally:
        for ref, tmp in segments:
            os.unlink(tmp)
    assert media.get_file_data.call_args_list == [
        ((11,),),
        ((12,),),
    ]


def test_resolve_recording_segments_single_entry_matches_single_resolver(tmp_path):
    """Single-segment payloads keep the exact single-file resolution path."""
    media = Mock()
    media.get_file_data.return_value = (b"audio-bytes", "audio/webm")
    payload = {
        "recording_ref": "/api/v1/media/attachments/recording.webm",
        "recording_file_id": 42,
        # A stale single-element list must NOT trigger multi-segment handling.
        "recording_file_ids": [42],
    }
    with patch("src.api.get_media", return_value=media):
        segments = _resolve_recording_segments(
            payload, db=Mock(), provider_name="local_whisper", call_id=5
        )

    assert len(segments) == 1
    try:
        assert segments[0][0] == segments[0][1]
        with open(segments[0][0], "rb") as handle:
            assert handle.read() == b"audio-bytes"
    finally:
        os.unlink(segments[0][1])
    media.get_file_data.assert_called_once_with(42)


def test_merge_transcription_results_offsets_later_segment_timestamps():
    """M27: merged timestamps continue from the previous segment's end."""
    first = TranscriptionResult(
        segments=[
            {"start": 0.0, "end": 4.0, "speaker": None, "text": "first part"},
        ],
        language="en",
        text="first part",
    )
    second = TranscriptionResult(
        segments=[
            {"start": 0.0, "end": 1.5, "speaker": None, "text": "second part"},
        ],
        language="en",
        text="second part",
    )

    merged = _merge_transcription_results([first, second])

    assert merged.segments == [
        {"start": 0.0, "end": 4.0, "speaker": None, "text": "first part"},
        {"start": 4.0, "end": 5.5, "speaker": None, "text": "second part"},
    ]
    assert merged.text == "first part second part"
    assert merged.language == "en"


def test_merge_transcription_results_single_result_returned_untouched():
    result = TranscriptionResult(
        segments=[{"start": 0.0, "end": 1.0, "text": "only"}],
        language="de",
        text="only",
    )
    assert _merge_transcription_results([result]) is result


@pytest.mark.asyncio
async def test_transcribe_call_merges_multi_segment_recordings(monkeypatch):
    """M27: transcribe_call runs the provider once per segment and persists a
    single merged transcript covering the whole call."""
    from src.core.artifacts.transcription import worker as w

    call = SimpleNamespace(
        id=5,
        artifact_id=10,
        recorded=True,
        initiator_id=1,
        conversation_id=100,
        channel_id=None,
        server_id=None,
        duration_seconds=30,
        consented_participants=[1, 2],
    )
    artifact = SimpleNamespace(
        payload={
            "participant_ids": [1, 2],
            "recording_ref": "/api/v1/media/attachments/call_5.webm",
            "recording_file_id": 11,
            "recording_file_ids": [11, 12],
        }
    )

    monkeypatch.setattr(w, "_capability_available", lambda: True)
    monkeypatch.setattr(w, "get_voice_call", lambda db, cid: call)
    monkeypatch.setattr(w, "get_artifact", lambda db, aid: artifact)
    monkeypatch.setattr(w, "_consent_allows", lambda call, roster: True)
    monkeypatch.setattr(
        w,
        "_resolve_recording_segments",
        lambda payload, db, provider, call_id=None: [
            ("/tmp/seg-0.webm", None),
            ("/tmp/seg-1.webm", None),
        ],
    )
    monkeypatch.setattr(w, "_emit_artifact_update", lambda *a, **k: None)

    provider = Mock()
    provider.is_available.return_value = True
    provider.transcribe = AsyncMock(
        side_effect=[
            TranscriptionResult(
                segments=[{"start": 0.0, "end": 2.0, "text": "hello"}],
                language="en",
                text="hello",
            ),
            TranscriptionResult(
                segments=[{"start": 0.0, "end": 3.0, "text": "world"}],
                language="en",
                text="world",
            ),
        ]
    )
    monkeypatch.setattr(w, "get_transcription_provider", lambda cfg: provider)

    created = {}

    class FakeArtifactManager:
        def __init__(self, db, config):
            pass

        def create(self, **kwargs):
            created.update(kwargs)
            return SimpleNamespace(id=999)

    class FakeVoiceCallManager:
        def __init__(self, db, artifact_manager=None, config=None):
            pass

        def set_transcript(self, call_id, artifact_id):
            created["set_transcript"] = (call_id, artifact_id)

    monkeypatch.setattr(w, "ArtifactManager", FakeArtifactManager)
    monkeypatch.setattr(w, "VoiceCallManager", FakeVoiceCallManager)

    result_id = await w.transcribe_call(
        5, Mock(), config_arg={"provider": "local_whisper"}
    )

    assert result_id == 999
    assert provider.transcribe.await_count == 2
    assert provider.transcribe.await_args_list[0].args[0] == "/tmp/seg-0.webm"
    assert provider.transcribe.await_args_list[1].args[0] == "/tmp/seg-1.webm"
    payload = created["payload"]
    assert payload["segments"] == [
        {"start": 0.0, "end": 2.0, "text": "hello"},
        {"start": 2.0, "end": 5.0, "text": "world"},
    ]
    assert payload["text"] == "hello world"
    assert created["set_transcript"] == (5, 999)


def test_resolve_recording_ref_cleans_partial_file_after_stream_failure(
    monkeypatch, tmp_path
):
    class FailingStream:
        def read(self, _size=-1):
            raise OSError("read failed")

        def close(self):
            return None

    media = Mock()
    media.get_file_stream.return_value = (FailingStream(), 10, "audio/webm")
    original_named_temporary_file = transcription_worker.tempfile.NamedTemporaryFile

    def named_temporary_file(**kwargs):
        return original_named_temporary_file(dir=tmp_path, **kwargs)

    monkeypatch.setattr(
        transcription_worker.tempfile,
        "NamedTemporaryFile",
        named_temporary_file,
    )
    with patch("src.api.get_media", return_value=media):
        resolved, temporary = _resolve_recording_ref(
            {
                "recording_ref": "/api/v1/media/attachments/recording.webm",
                "recording_file_id": 42,
            },
            db=Mock(),
            provider_name="local_whisper",
        )

    assert resolved is None
    assert temporary is None
    assert list(tmp_path.iterdir()) == []


def test_resolve_recording_ref_rejects_azure_without_public_origin(monkeypatch):
    monkeypatch.delenv("PLEXICHAT_PUBLIC_SERVER_URL", raising=False)
    with patch("src.api.get_media") as get_media:
        resolved, temporary = _resolve_recording_ref(
            {
                "recording_ref": "/api/v1/media/attachments/recording.webm",
                "recording_file_id": 42,
            },
            db=Mock(),
            provider_name="azure",
        )

    assert resolved is None
    assert temporary is None
    get_media.assert_not_called()


@pytest.mark.asyncio
async def test_azure_uses_rest_for_remote_references_even_with_sdk():
    provider = AzureSpeechProvider({"azure_key": "test-key"})
    expected = TranscriptionResult(language="en-US", text="remote")
    with (
        patch.object(provider, "_sdk_module", object()),
        patch.object(
            provider,
            "_transcribe_sdk",
            side_effect=AssertionError("SDK must not run"),
        ) as sdk,
        patch.object(provider, "_transcribe_rest", return_value=expected) as rest,
    ):
        result = await provider.transcribe("https://chat.example.test/audio.webm", {})

    assert result is expected
    rest.assert_called_once()
    sdk.assert_not_called()


def test_azure_timespan_parses_iso8601_durations():
    assert azure_timespan_to_ms("PT0.05S") == 50
    assert azure_timespan_to_ms("PT1S") == 1000
    assert azure_timespan_to_ms("PT1.5M") == 90000
    assert azure_timespan_to_ms("PT1H2M3S") == 3723000
    assert azure_timespan_to_ms("P1DT0.5S") == 86400500


def test_azure_timespan_parses_tick_strings_and_numbers():
    # Legacy tick strings: 10,000,000 ticks == 1 second.
    assert azure_timespan_to_ms("10000000") == 1000
    assert azure_timespan_to_ms("5000000") == 500
    assert azure_timespan_to_ms(20000000) == 2000
    assert azure_timespan_to_ms(2500000.0) == 250


def test_azure_timespan_tolerates_suffixes_and_garbage():
    assert azure_timespan_to_ms(None) == 0
    assert azure_timespan_to_ms("") == 0
    assert azure_timespan_to_ms("10000000Z") == 1000
    assert azure_timespan_to_ms("not-a-duration") == 0
    assert azure_timespan_to_ms(True) == 0


def test_azure_segment_builder_handles_v31_iso_offsets():
    provider = AzureSpeechProvider({"azure_key": "test-key"})
    result = provider._build_segments_from_results(
        [
            {
                "Offset": "PT0.05S",
                "Duration": "PT0.20S",
                "DisplayText": "Hello world.",
            }
        ]
    )
    assert result.segments[0]["start"] == 0.05
    assert result.segments[0]["end"] == 0.25
    assert result.text == "Hello world."


def test_transcription_consent_requires_unanimous_participants():
    """Transcription follows recording's all-participants consent policy."""
    from types import SimpleNamespace

    from src.core.artifacts.transcription.worker import _consent_allows

    assert (
        _consent_allows(SimpleNamespace(consented_participants=[], participant_count=0))
        is False
    )
    assert (
        _consent_allows(
            SimpleNamespace(consented_participants=[33], participant_count=1)
        )
        is True
    )
    # Two joined, only one consented: must NOT transcribe (was >=1 before).
    assert (
        _consent_allows(
            SimpleNamespace(consented_participants=[33], participant_count=2)
        )
        is False
    )
    assert (
        _consent_allows(
            SimpleNamespace(consented_participants=[33, 44], participant_count=2)
        )
        is True
    )
    # Malformed rosters fail closed.
    assert (
        _consent_allows(
            SimpleNamespace(consented_participants="nope", participant_count=1)
        )
        is False
    )


def test_transcription_consent_roster_blocks_member_churn():
    """The persisted distinct-ID roster defeats count-based churn attacks."""
    from types import SimpleNamespace

    from src.core.artifacts.transcription.worker import _consent_allows

    # Churn: A/B/C joined (count 3); A+B consented then left; C consented.
    call = SimpleNamespace(consented_participants=[11, 22, 33], participant_count=3)
    # Legacy count check PASSES here...
    assert len(set(call.consented_participants)) >= call.participant_count
    # ...but the persisted roster contains the never-consenting joiner 44.
    assert _consent_allows(call, [11, 22, 33, 44]) is False
    # Full coverage transcribes; empty/malformed rosters fail closed.
    assert _consent_allows(call, [11, 22, 33]) is True
    assert _consent_allows(call, []) is False
    assert _consent_allows(call, ["bad"]) is False
    # Stale consents from departed users do not cover a missing member.
    assert (
        _consent_allows(
            SimpleNamespace(consented_participants=[11, 22, 99], participant_count=3),
            [11, 22, 33],
        )
        is False
    )
