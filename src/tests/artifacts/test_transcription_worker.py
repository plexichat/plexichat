"""Tests for transcription recording-reference normalization."""

from unittest.mock import Mock, patch

import pytest

from src.core.artifacts.transcription.provider import (
    AzureSpeechProvider,
    TranscriptionResult,
    azure_timespan_to_ms,
)
from src.core.artifacts.transcription import worker as transcription_worker
from src.core.artifacts.transcription.worker import _resolve_recording_ref


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
