# Voice-call transcription

This document describes the voice-call transcription framework. Use it to turn a finished, recorded voice call into a timestamped transcript artifact.

## Overview

The framework converts a recorded call into a `TRANSCRIPT` artifact that contains speaker-attributed segments. It has two parts:

*   `provider.py` — Abstract `TranscriptionProvider` and three concrete backends.
*   `worker.py` — Gating, recording resolution, provider execution, and artifact creation.

Transcription is gated by config, license, and participant consent. If any gate fails, the worker logs the reason and does not produce a transcript.

## Providers

All providers implement `TranscriptionProvider` and return a `TranscriptionResult` with `segments` (`{start, end, speaker, text}`), `language`, and flattened `text`.

| Provider | When it is used | Availability check |
| --- | --- | --- |
| `LocalWhisperProvider` | `provider = local_whisper`. Runs OpenAI Whisper in-process. Model is loaded lazily with `model_size` from config. | `True` if `whisper` or `openai_whisper` is importable, the model size is valid, and `load_model` exists. |
| `OpenAIWhisperProvider` | `provider = openai`. Calls `https://api.openai.com/v1/audio/transcriptions` with `verbose_json` and `segment` granularity. | `True` if `openai_api_key` is non-empty. |
| `AzureSpeechProvider` | `provider = azure`. Uses the `azure-cognitiveservices-speech` SDK when installed, otherwise falls back to the REST batch transcription v3.1 API with polling. | `True` if `azure_key` is non-empty. |

### Provider selection

`get_transcription_provider(config)` is the only place that selects a provider. It reads `artifacts.voice.transcription` and raises `ValueError` if the provider is unknown or a required key is missing. The capability service converts that error to a `misconfigured` state.

```python
from src.core.artifacts.transcription.provider import get_transcription_provider

provider = get_transcription_provider(config["artifacts"]["voice"]["transcription"])
if provider.is_available():
    result = await provider.transcribe(recording_ref, opts)
```

### Local Whisper details

*   Whisper is discovered with `importlib.util.find_spec("whisper")` or `find_spec("openai_whisper")`.
*   `is_available()` caches its result. After a successful `transcribe`, availability is permanently `True`.
*   Speaker diarization runs only if `diarize` is `True` and a diarization library (`pyannote.audio`, `speechbrain`, or `nemo.collections.asr`) is installed. Otherwise every segment receives `speaker = "unknown"`. Diarization failures are memoized so transcription continues with unknown speakers instead of retrying discovery on every call.
*   Transcription runs in the default executor with `asyncio.to_thread` so the event loop is not blocked.

### OpenAI Whisper details

*   Requires a local file path for `recording_ref`. The file is uploaded as `multipart/form-data` with `model`, `response_format = verbose_json`, and `timestamp_granularities[] = segment`.
*   The request timeout is 600 seconds. The response `segments` are normalized to `{start, end, speaker, text}`. If no segments are present but `text` exists, a single segment with `start = 0`, `end = 0`, and `speaker = unknown` is created.

### Azure Speech details

*   Local file paths use the SDK path with `SpeechConfig` and `AudioConfig`. HTTPS URLs use the REST batch API.
*   `_validate_azure_content_url` rejects non-HTTPS URLs and private or loopback destinations. Both the recording URL and the returned transcription content URL are validated before fetching.
*   Azure offsets and durations are normalized with `azure_timespan_to_ms`, which handles ISO-8601 durations (`PT0.05S`), 100-nanosecond tick strings or numbers, and trailing `Z` suffixes. Malformed values return `0` instead of raising.
*   REST flow creates a transcription, polls status up to 60 times every 2 seconds, lists files, downloads the `Transcription` content URL, and parses `recognizedPhrases` into segments.

### Result serialization

```python
result = TranscriptionResult(segments=[...], language="en", text="hello world")
payload = result.to_payload()
# {"segments": [...], "language": "en", "text": "hello world"}
```

## Worker

Defined in `worker.py`.

| Function | Description |
| --- | --- |
| `transcribe_call(call_id, db, config)` | Loads the `voice_calls` row, checks gating, resolves `recording_ref` from the linked `voice_call` artifact payload, runs the provider, creates a `TRANSCRIPT` artifact, links it with `VoiceCallManager.set_transcript`, and emits `ARTIFACT_UPDATE`. Never raises to the caller. Logs failures and leaves the call without a transcript. |
| `schedule_transcribe_call(call_id, db, config)` | Fire-and-forget scheduler. Uses `asyncio.create_task` when a loop is running, otherwise enqueues on a bounded in-process queue. |
| `ensure_transcription_drainer()` | Starts the queue drainer when a loop is available. Called at startup. |

## Gating

Transcription runs only when all of the following are `True`:

1.  `artifacts.voice.transcription.enabled` is `True`.
2.  `artifacts.voice.transcription.auto_transcribe` is `True`.
3.  The `voice_transcription` capability is `available`. The capability service in `src/core/artifacts/capabilities.py` is the single source of truth. It reuses `provider.is_available()` so `dependency_missing`, `misconfigured`, and `disabled` are computed once and shared by the worker and admin panel.
4.  The call was `recorded`.
5.  Consent is satisfied. When `consent_required` is `True` (default), every distinct participant must have consented (unanimous consent). The check verifies that the distinct `participant_ids` roster stored in the linked artifact payload is a subset of `consented_participants`. Calls without a roster fall back to comparing the consent count against `participant_count`. Calls that lack unanimous consent are skipped.

When the capability is not `available`, the worker does not attempt transcription.

Auto-wiring: when `VoiceCallManager.end_call` completes and transcription is enabled, `auto_transcribe` is on, and the license is present, the transcription job is scheduled automatically. The hook lives in `src/core/voice/manager/calls.py`.

## Recording references

The worker resolves audio from `recording_ref` stored in the linked `voice_call` artifact's `payload`. If the reference is absent, the worker logs the condition and skips the call. Storing the reference in the artifact payload keeps the schema unchanged and lets the voice module populate it when a recording is finalized.

## Data portability

`src/core/dsar/collector.py` includes for a user:

*   `voice_calls` where the user is the initiator or a consented participant.
*   `artifacts` of type `voice_call` or `transcript` authored by the user, with transcript text surfaced inline as `transcript_text`.

Deletion is handled at the manager layer. The collector only handles the export.

## Configuration

Set values under `config["artifacts"]["voice"]["transcription"]`.

| Key | Default | Description |
| --- | --- | --- |
| `provider` | `local_whisper` | `local_whisper`, `openai`, or `azure`. |
| `enabled` | `False` | Master switch for transcription. |
| `auto_transcribe` | `False` | Transcribe automatically when a call ends. |
| `language` | `auto` | Language hint. Passed to Whisper or Azure. `auto` omits the hint. |
| `diarize` | `False` | Enable speaker attribution when a diarization library is available. |
| `model_size` | `base` | Whisper model size, for example `tiny`, `base`, `small`, `medium`, `large-v3`. |
| `whisper_probe_on_startup` | `True` | Probe Whisper availability at boot. |
| `openai_api_key` | `${OPENAI_API_KEY:-}` | API key for the OpenAI provider. |
| `azure_key` | `${AZURE_SPEECH_KEY:-}` | Key for the Azure provider. |
| `max_audio_minutes` | `120` | Soft cap on audio length in minutes. |

Additional provider keys: `openai_model` (default `whisper-1`), `openai_api_base`, `azure_region` (default `eastus`).

## Related code

*   `plexichat/src/core/artifacts/transcription/provider.py:127` — `TranscriptionProvider`
*   `plexichat/src/core/artifacts/transcription/provider.py:90` — `TranscriptionResult`
*   `plexichat/src/core/artifacts/transcription/provider.py:719` — `get_transcription_provider`
*   `plexichat/src/core/artifacts/transcription/worker.py` — `transcribe_call`, `schedule_transcribe_call`
*   `plexichat/src/core/artifacts/capabilities.py` — `voice_transcription` capability
