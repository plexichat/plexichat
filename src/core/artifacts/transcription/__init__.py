"""
Voice-call transcription framework.

Exposes the provider abstraction (:class:`TranscriptionProvider` and its
concrete backends) plus the async worker that turns a finished voice call into a
transcript artifact.

Provider selection is centralized in :func:`get_transcription_provider`, which
is the only place that decides *which* backend is wired up from config. The
capability service reuses :func:`get_transcription_provider` so there is a single
source of truth for the ``voice_transcription`` availability state.
"""

from .provider import (
    TranscriptionResult,
    TranscriptionProvider,
    LocalWhisperProvider,
    OpenAIWhisperProvider,
    AzureSpeechProvider,
    get_transcription_provider,
)
from .worker import transcribe_call, schedule_transcribe_call


__all__ = [
    "TranscriptionResult",
    "TranscriptionProvider",
    "LocalWhisperProvider",
    "OpenAIWhisperProvider",
    "AzureSpeechProvider",
    "get_transcription_provider",
    "transcribe_call",
    "schedule_transcribe_call",
]
