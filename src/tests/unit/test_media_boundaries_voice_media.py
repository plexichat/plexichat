"""Transcription and recording payloads are size- and id-bounded."""

import pytest

from src.core.voice.boundaries import (
    MAX_RECORDING_ID_LENGTH,
    MAX_TRANSCRIPT_SEGMENTS,
    MAX_TRANSCRIPT_TEXT_LENGTH,
    validate_recording_id,
    validate_transcription_config,
    validate_transcription_payload,
)


class TestRecordingIds:
    def test_valid_id(self):
        assert validate_recording_id("rec_voice_123") == "rec_voice_123"

    @pytest.mark.parametrize(
        "value",
        ["", "x" * (MAX_RECORDING_ID_LENGTH + 1), "../evil", "a/b", "a\\b", "a\x00b"],
    )
    def test_invalid_id_rejected(self, value):
        with pytest.raises(ValueError):
            validate_recording_id(value)


class TestTranscriptionConfig:
    def test_valid_config(self):
        cfg = validate_transcription_config(
            {"provider": "whisper", "language": "en", "chunk_seconds": 30}
        )
        assert cfg["provider"] == "whisper"
        assert cfg["chunk_seconds"] == 30

    def test_non_object_rejected(self):
        with pytest.raises(ValueError):
            validate_transcription_config("whisper")

    def test_oversized_string_rejected(self):
        with pytest.raises(ValueError):
            validate_transcription_config({"provider": "x" * 1000})

    def test_invalid_number_rejected(self):
        with pytest.raises(ValueError):
            validate_transcription_config({"chunk_seconds": -5})


class TestTranscriptionPayload:
    def test_valid_payload(self):
        result = validate_transcription_payload(
            {"call_id": 123, "segments": [{"text": "hello"}, {"text": "world"}]}
        )
        assert result["call_id"] == 123
        assert result["total_chars"] == 10

    def test_non_object_rejected(self):
        with pytest.raises(ValueError):
            validate_transcription_payload(["hello"])

    def test_too_many_segments_rejected(self):
        with pytest.raises(ValueError, match="too many segments"):
            validate_transcription_payload(
                {"segments": [{"text": "x"}] * (MAX_TRANSCRIPT_SEGMENTS + 1)}
            )

    def test_oversized_segment_rejected(self):
        with pytest.raises(ValueError, match="too large"):
            validate_transcription_payload(
                {"segments": [{"text": "x" * (MAX_TRANSCRIPT_TEXT_LENGTH + 1)}]}
            )

    def test_invalid_call_id_rejected(self):
        with pytest.raises(ValueError):
            validate_transcription_payload({"call_id": -1, "segments": []})

    def test_invalid_recording_id_rejected(self):
        with pytest.raises(ValueError):
            validate_transcription_payload({"recording_id": "../evil", "segments": []})
