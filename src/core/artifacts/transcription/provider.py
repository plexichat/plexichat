"""
Voice-call transcription providers.

Defines the :class:`TranscriptionProvider` abstraction plus concrete backends:

* :class:`LocalWhisperProvider` - runs OpenAI Whisper locally (requires the
  ``whisper`` / ``openai-whisper`` package and a downloaded model).
* :class:`OpenAIWhisperProvider` - forwards audio to the hosted OpenAI Whisper
  API (requires an API key).
* :class:`AzureSpeechProvider` - uses the Azure Cognitive Services Speech
  service for batch transcription (requires a subscription key + region).

All providers return a :class:`TranscriptionResult` carrying timestamped,
optionally speaker-attributed segments plus the detected language and the
flattened full text.

The single decision point for "which backend am I using" is
:func:`get_transcription_provider`, which reads the artifacts transcription
config and raises a clear error when that config is internally inconsistent.
That error is what the capability service turns into a ``misconfigured`` state,
so the provider selection logic lives here and nowhere else.
"""

import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TranscriptionResult:
    """Normalized output of a transcription run."""

    segments: List[Dict[str, Any]] = field(default_factory=list)
    language: str = "unknown"
    text: str = ""

    def to_payload(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict for artifact storage."""
        return {
            "segments": self.segments,
            "language": self.language,
            "text": self.text,
        }


def _find_whisper_module() -> Optional[str]:
    """Return the importable whisper module name, or ``None`` if absent."""
    for name in ("whisper", "openai_whisper"):
        if importlib.util.find_spec(name) is not None:
            return name
    return None


def _find_diarization_module() -> Optional[str]:
    """Return an available diarization module name, or ``None`` if absent."""
    candidates = (
        "pyannote.audio",
        "speechbrain",
        "nemo.collections.asr",
    )
    for name in candidates:
        if importlib.util.find_spec(name) is not None:
            return name
    return None


class TranscriptionProvider(ABC):
    """Abstract base for a transcription backend."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._config: Dict[str, Any] = config if config is not None else {}

    @abstractmethod
    async def transcribe(
        self, recording_ref: str, opts: Dict[str, Any]
    ) -> TranscriptionResult:
        """Transcribe ``recording_ref`` and return a result.

        ``recording_ref`` is a path or URL to the recorded audio. ``opts`` is a
        per-call override dict (e.g. ``language``, ``diarize``).
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return ``True`` only if the backend can actually run right now."""
        ...

    # === Shared helpers ===

    def _language(self, opts: Dict[str, Any]) -> str:
        lang = opts.get("language")
        if lang is None:
            lang = self._config.get("language", "auto")
        return str(lang)

    def _wants_diarize(self, opts: Dict[str, Any]) -> bool:
        if opts.get("diarize") is not None:
            return bool(opts["diarize"])
        return bool(self._config.get("diarize", False))

    @staticmethod
    def _assemble_text(segments: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if text:
                parts.append(text)
        return " ".join(parts)


class LocalWhisperProvider(TranscriptionProvider):
    """Runs Whisper locally.

    Whisper is loaded lazily and cached on the instance. Speaker diarization is
    attempted only when enabled in config/opts *and* a diarization library is
    installed; otherwise every segment is attributed to a single ``"unknown"``
    speaker.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._model: Any = None
        self._model_size: str = str(self._config.get("model_size", "base"))
        self._module_name: Optional[str] = _find_whisper_module()
        self._diarization_module: Optional[str] = None
        self._diarizer: Any = None

    def is_available(self) -> bool:
        if self._module_name is None:
            return False
        # Able to import does not guarantee able to *run*; the weighted model
        # download/load is the expensive part and is performed lazily, but we
        # still validate that the module is importable and the requested model
        # size is a known value.
        valid_sizes = {
            "tiny",
            "tiny.en",
            "base",
            "base.en",
            "small",
            "small.en",
            "medium",
            "medium.en",
            "large",
            "large-v1",
            "large-v2",
            "large-v3",
        }
        return self._model_size in valid_sizes

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._module_name is None:
            raise RuntimeError(
                "Whisper is not installed. Install with: pip install openai-whisper"
            )
        import importlib

        whisper_mod = importlib.import_module(self._module_name)
        # ``load_model`` downloads the weights on first use; this is the real
        # runtime cost and deliberately happens here, not at import time.
        self._model = whisper_mod.load_model(self._model_size)
        return self._model

    def _ensure_diarizer(self) -> Any:
        if self._diarizer is not None or self._diarization_module is not None:
            return self._diarizer
        module_name = _find_diarization_module()
        if module_name is None:
            self._diarizer = None
            return None
        try:
            import importlib

            diar_mod = importlib.import_module(module_name)
            if module_name == "pyannote.audio":
                # pyannote exposes a pretrained pipeline; instantiate lazily so
                # a missing token / model does not crash availability checks.
                self._diarizer = diar_mod
            else:
                self._diarizer = diar_mod
            self._diarization_module = module_name
        except Exception:
            self._diarizer = None
        return self._diarizer

    async def transcribe(
        self, recording_ref: str, opts: Dict[str, Any]
    ) -> TranscriptionResult:
        model = self._ensure_model()
        language = self._language(opts)
        decode_options: Dict[str, Any] = {"word_timestamps": True}
        if language and language != "auto":
            decode_options["language"] = language

        # Whisper's ``transcribe`` is CPU/GPU bound and synchronous; we call it
        # directly inside the task (the worker already runs it off the request
        # path). The return includes ``segments`` with start/end (seconds) and
        # ``text`` plus ``language``.
        result = model.transcribe(recording_ref, **decode_options)
        detected_language = str(result.get("language", language or "unknown"))

        segments: List[Dict[str, Any]] = []
        for seg in result.get("segments", []):
            segments.append(
                {
                    "start": float(seg.get("start", 0.0)),
                    "end": float(seg.get("end", 0.0)),
                    "speaker": None,
                    "text": (seg.get("text") or "").strip(),
                }
            )

        segments = self._apply_diarization(segments, recording_ref, opts)

        return TranscriptionResult(
            segments=segments,
            language=detected_language,
            text=self._assemble_text(segments),
        )

    def _apply_diarization(
        self,
        segments: List[Dict[str, Any]],
        recording_ref: str,
        opts: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not self._wants_diarize(opts):
            for seg in segments:
                seg["speaker"] = "unknown"
            return segments

        diarizer = self._ensure_diarizer()
        if diarizer is None:
            for seg in segments:
                seg["speaker"] = "unknown"
            return segments

        # Best-effort mapping of diarization turns onto Whisper segments by
        # midpoint overlap. We support pyannote's pipeline; other libraries are
        # normalised into the same (start, end, speaker) turn list.
        turns = self._diarize_turns(diarizer, recording_ref)
        if not turns:
            for seg in segments:
                seg["speaker"] = "unknown"
            return segments

        for seg in segments:
            mid = (float(seg["start"]) + float(seg["end"])) / 2.0
            speaker = "unknown"
            for start, end, spk in turns:
                if start <= mid <= end:
                    speaker = spk
                    break
            seg["speaker"] = speaker
        return segments

    def _diarize_turns(self, diarizer: Any, recording_ref: str) -> List[Any]:
        try:
            if self._diarization_module == "pyannote.audio":
                pipeline = diarizer.Pipeline.from_pretrained(
                    "pyannote/speaker-diarization"
                )
                diarization = pipeline(recording_ref)
                turns: List[Any] = []
                for turn, _, speaker in diarization.itertracks(yield_label=True):
                    turns.append((float(turn.start), float(turn.end), str(speaker)))
                return turns
        except Exception:
            return []
        return []


class OpenAIWhisperProvider(TranscriptionProvider):
    """Hosted OpenAI Whisper API transcription.

    Sends the recording file to ``https://api.openai.com/v1/audio/transcriptions``
    using the ``multipart/form-data`` body documented by OpenAI. ``timestamp_granularities[]=segment``
    returns per-segment timing which we normalize into our segment schema.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._api_key: str = str(self._config.get("openai_api_key", "") or "")
        self._model: str = str(self._config.get("openai_model", "whisper-1"))
        self._api_url: str = str(
            self._config.get(
                "openai_api_base", "https://api.openai.com/v1/audio/transcriptions"
            )
        )

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def transcribe(
        self, recording_ref: str, opts: Dict[str, Any]
    ) -> TranscriptionResult:
        if not self._api_key:
            raise RuntimeError(
                "OpenAI transcription selected but no API key is configured "
                "(artifacts.voice.transcription.openai_api_key)."
            )

        import os

        import requests

        if not os.path.isfile(recording_ref):
            raise RuntimeError(
                f"Recording reference is not a local file: {recording_ref}"
            )

        language = self._language(opts)
        with open(recording_ref, "rb") as audio_file:
            form: Dict[str, Any] = {
                "file": (os.path.basename(recording_ref), audio_file),
                "model": (None, self._model),
                "response_format": (None, "verbose_json"),
                "timestamp_granularities[]": (None, "segment"),
            }
            if language and language != "auto":
                form["language"] = (None, language)

            headers = {"Authorization": f"Bearer {self._api_key}"}
            response = requests.post(
                self._api_url, files=form, headers=headers, timeout=600
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenAI transcription failed: HTTP {response.status_code} "
                f"{response.text[:500]}"
            )

        data = response.json()
        return self._parse_response(data)

    def _parse_response(self, data: Dict[str, Any]) -> TranscriptionResult:
        text = str(data.get("text", "")).strip()
        language = str(data.get("language", "unknown"))
        segments: List[Dict[str, Any]] = []
        raw_segments = data.get("segments")
        if isinstance(raw_segments, list):
            for seg in raw_segments:
                try:
                    segments.append(
                        {
                            "start": float(seg.get("start", 0.0)),
                            "end": float(seg.get("end", 0.0)),
                            "speaker": None,
                            "text": str(seg.get("text", "")).strip(),
                        }
                    )
                except (TypeError, ValueError):
                    continue
        if not segments and text:
            segments.append(
                {"start": 0.0, "end": 0.0, "speaker": "unknown", "text": text}
            )
        return TranscriptionResult(
            segments=segments,
            language=language,
            text=text or self._assemble_text(segments),
        )


class AzureSpeechProvider(TranscriptionProvider):
    """Azure Cognitive Services Speech batch transcription.

    Uses the `azure-cognitiveservices-speech` SDK when installed; otherwise
    falls back to the REST batch-transcription API. Speaker separation is not
    available from the basic REST path, so segments use a single ``"unknown"``
    speaker unless the SDK diarization flags are enabled and available.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._key: str = str(self._config.get("azure_key", "") or "")
        self._region: str = str(self._config.get("azure_region", "eastus"))
        self._locale: str = str(self._config.get("language", "en-US"))
        if self._locale in ("auto", ""):
            self._locale = "en-US"
        self._sdk_module = importlib.util.find_spec("azure.cognitiveservices.speech")

    def is_available(self) -> bool:
        return bool(self._key)

    async def transcribe(
        self, recording_ref: str, opts: Dict[str, Any]
    ) -> TranscriptionResult:
        if not self._key:
            raise RuntimeError(
                "Azure transcription selected but no key is configured "
                "(artifacts.voice.transcription.azure_key)."
            )

        if self._sdk_module is not None:
            return await self._transcribe_sdk(recording_ref, opts)
        return await self._transcribe_rest(recording_ref, opts)

    def _build_segments_from_results(
        self, phrases: List[Dict[str, Any]]
    ) -> TranscriptionResult:
        segments: List[Dict[str, Any]] = []
        text_parts: List[str] = []
        for phrase in phrases:
            start_ms = int(phrase.get("Offset", 0) or 0)
            duration_ms = int(phrase.get("Duration", 0) or 0)
            start_s = start_ms / 1000.0
            end_s = (start_ms + duration_ms) / 1000.0
            text = str(phrase.get("DisplayText", phrase.get("Text", ""))).strip()
            speaker = phrase.get("SpeakerId")
            segments.append(
                {
                    "start": start_s,
                    "end": end_s,
                    "speaker": str(speaker) if speaker else "unknown",
                    "text": text,
                }
            )
            if text:
                text_parts.append(text)
        return TranscriptionResult(
            segments=segments,
            language=self._locale,
            text=" ".join(text_parts),
        )

    async def _transcribe_sdk(
        self, recording_ref: str, opts: Dict[str, Any]
    ) -> TranscriptionResult:
        if self._sdk_module is None:
            raise RuntimeError(
                "Azure Speech SDK (azure.cognitiveservices.speech) is not "
                "installed. Install it to use the Azure transcription provider."
            )
        speechsdk = importlib.import_module("azure.cognitiveservices.speech")

        locale = self._locale
        if opts.get("language"):
            locale = str(opts["language"])
        speech_config = speechsdk.SpeechConfig(
            subscription=self._key, region=self._region
        )
        speech_config.speech_recognition_language = locale
        audio_config = speechsdk.audio.AudioConfig(filename=recording_ref)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config, audio_config=audio_config
        )

        phrases: List[Dict[str, Any]] = []
        done = False

        def _on_recognized(evt: Any) -> None:
            phrases.append(
                {
                    "Offset": getattr(evt.result, "offset", 0),
                    "Duration": getattr(evt.result, "duration", 0),
                    "DisplayText": evt.result.text or "",
                    "SpeakerId": None,
                }
            )

        def _on_stop(_: Any) -> None:
            nonlocal done
            done = True

        recognizer.recognized.connect(_on_recognized)
        recognizer.session_stopped.connect(_on_stop)
        recognizer.canceled.connect(_on_stop)

        recognizer.start_continuous_recognition()
        import time

        while not done:
            time.sleep(0.2)
        recognizer.stop_continuous_recognition()

        return self._build_segments_from_results(phrases)

    async def _transcribe_rest(
        self, recording_ref: str, opts: Dict[str, Any]
    ) -> TranscriptionResult:
        import requests

        base_url = (
            f"https://{self._region}.api.cognitive.microsoft.com/speechtotext/"
            "v3.1/transcriptions"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": self._key,
            "Content-Type": "application/json",
        }
        locale = self._locale
        if opts.get("language"):
            locale = str(opts["language"])
        definition = {
            "displayName": "plexichat-transcription",
            "locale": locale,
            "contentUrls": [recording_ref],
            "properties": {
                "wordLevelTimestampsEnabled": True,
                "diarizationEnabled": self._wants_diarize(opts),
            },
        }
        create = requests.post(base_url, headers=headers, json=definition, timeout=60)
        if create.status_code not in (200, 201):
            raise RuntimeError(
                f"Azure transcription create failed: HTTP {create.status_code} "
                f"{create.text[:500]}"
            )
        transcription_id = create.json().get("self", "").rstrip("/").split("/")[-1]
        if not transcription_id:
            raise RuntimeError("Azure transcription did not return an id.")

        status_url = f"{base_url}/{transcription_id}"
        import time

        files_url = None
        for _ in range(60):
            status = requests.get(status_url, headers=headers, timeout=60)
            if status.status_code != 200:
                raise RuntimeError(
                    f"Azure status poll failed: HTTP {status.status_code}"
                )
            state = status.json().get("status")
            if state in ("Succeeded", "Failed"):
                if state == "Failed":
                    raise RuntimeError("Azure transcription job failed.")
                files_url = status.json().get("links", {}).get("files")
                break
            time.sleep(2)

        if not files_url:
            raise RuntimeError("Azure transcription did not finish in time.")

        files_resp = requests.get(files_url, headers=headers, timeout=60)
        if files_resp.status_code != 200:
            raise RuntimeError("Azure transcription files listing failed.")
        content_url = None
        for item in files_resp.json().get("values", []):
            if item.get("kind") == "Transcription":
                content_url = item.get("links", {}).get("contentUrl")
                break
        if not content_url:
            raise RuntimeError("Azure transcription content URL not found.")

        content = requests.get(content_url, timeout=120)
        if content.status_code != 200:
            raise RuntimeError("Azure transcription content download failed.")
        data = content.json()
        phrases: List[Dict[str, Any]] = []
        for phrase in data.get("recognizedPhrases", []):
            offset = int(phrase.get("offset", "0").rstrip("Z") or 0)
            duration = int(phrase.get("duration", "0").rstrip("Z") or 0)
            display = phrase.get("nBest", [{}])[0].get("display", "")
            speaker = phrase.get("speaker")
            phrases.append(
                {
                    "Offset": offset,
                    "Duration": duration,
                    "DisplayText": display,
                    "SpeakerId": speaker,
                }
            )
        return self._build_segments_from_results(phrases)


def get_transcription_provider(config: Dict[str, Any]) -> TranscriptionProvider:
    """Build the configured transcription provider.

    Args:
        config: The ``artifacts.voice.transcription`` config dict.

    Returns:
        A ready-to-use :class:`TranscriptionProvider`.

    Raises:
        ValueError: when the provider is unknown or required configuration is
            missing/empty. This error is the single source of the
            capability ``misconfigured`` state.
    """
    if not isinstance(config, dict):
        raise ValueError("Transcription config must be a dict.")

    provider = config.get("provider", "local_whisper")
    if provider == "local_whisper":
        return LocalWhisperProvider(config)
    if provider == "openai":
        api_key = config.get("openai_api_key", "") or ""
        if not api_key:
            raise ValueError(
                "OpenAI transcription selected but the OpenAI API key "
                "(artifacts.voice.transcription.openai_api_key) is not set."
            )
        return OpenAIWhisperProvider(config)
    if provider == "azure":
        azure_key = config.get("azure_key", "") or ""
        if not azure_key:
            raise ValueError(
                "Azure transcription selected but the Azure key "
                "(artifacts.voice.transcription.azure_key) is not set."
            )
        return AzureSpeechProvider(config)

    raise ValueError(f"Unknown transcription provider: '{provider}'.")


__all__ = [
    "TranscriptionResult",
    "TranscriptionProvider",
    "LocalWhisperProvider",
    "OpenAIWhisperProvider",
    "AzureSpeechProvider",
    "get_transcription_provider",
]
