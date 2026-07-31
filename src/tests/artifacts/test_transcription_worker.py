"""Tests for transcription recording-reference normalization."""

from unittest.mock import Mock, patch

import pytest

from src.core.artifacts.transcription.provider import (
    AzureSpeechProvider,
    TranscriptionResult,
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
