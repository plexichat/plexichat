# Voice-Call Transcription

This package implements the voice-call transcription framework for Plexichat.
It turns a finished (recorded) voice call into a timestamped, optionally
speaker-attributed **transcript** artifact, gated by config, license, and
participant consent.

## Components

### `provider.py` — backends

- `TranscriptionProvider` — abstract base. Subclasses implement
  `async transcribe(recording_ref, opts) -> TranscriptionResult` and
  `is_available() -> bool`.
- `TranscriptionResult` — dataclass with `segments` (a list of
  `{start, end, speaker, text}` dicts), `language`, and `text` (flattened).
- `LocalWhisperProvider` — runs OpenAI Whisper in-process. Whisper is imported
  via `importlib.util.find_spec("whisper") or find_spec("openai_whisper")` and
  the model is loaded lazily (`model_size` from config). Produces word-timestamped
  segments. Speaker diarization is attempted only when `diarize` is enabled
  **and** a diarization library (`pyannote.audio`, `speechbrain`, or NeMo) is
  installed; otherwise every segment is attributed to `"unknown"`.
- `OpenAIWhisperProvider` — real HTTP call to the OpenAI Whisper API
  (`/v1/audio/transcriptions`, `verbose_json` + `segment` granularity) using
  `openai_api_key` from config. Parses segment timing into the result schema.
- `AzureSpeechProvider` — real Azure Speech transcription. Uses the
  `azure-cognitiveservices-speech` SDK when installed, otherwise falls back to
  the REST batch-transcription v3.1 API with polling. Speaker ids are carried
  through when the service returns them.
- `get_transcription_provider(config)` — the single decision point. Builds the
  configured provider and **raises `ValueError`** when the config is internally
  inconsistent (missing key, unknown provider). This error feeds the capability
  `misconfigured` state; it is the only place provider selection happens.

### `worker.py` — the worker

- `async transcribe_call(call_id, db, config)` — loads the `voice_calls` row,
  enforces the gating rules below, resolves the recording reference, runs the
  provider, creates a `TRANSCRIPT` artifact linked to the call, flags the call
  and its `voice_call` artifact, links the transcript via
  `VoiceCallManager.set_transcript`, and emits `ARTIFACT_UPDATE` scoped to the
  conversation/server. It never raises to the caller — failures are logged and
  the call is left without a transcript.
- `schedule_transcribe_call(call_id, db, config)` — fire-and-forget scheduler.
  Uses `asyncio.create_task` when a loop is running, otherwise enqueues the job
  on a bounded in-process queue drained by `ensure_transcription_drainer()`.
- `ensure_transcription_drainer()` — starts the queue drainer once a loop is up
  (wired into app startup).

## Gating (single source of truth)

Transcription only runs when **all** of the following hold:

1. `artifacts.voice.transcription.enabled` is `True`.
2. `artifacts.voice.transcription.auto_transcribe` is `True`.
3. The `voice_transcription` capability is `AVAILABLE`. The capability service
   (`src/core/artifacts/capabilities.py`) is the single source of truth: it
   reuses `get_transcription_provider(...).is_available()` so the
   `DEPENDENCY_MISSING` / `MISCONFIGURED` / `DISABLED` states are computed once
   and shared by the worker and the admin panel.
4. The call was `recorded`.
5. Participant consent is satisfied: if `consent_required` is `True` (default),
   the `consented_participants` list must be non-empty. GDPR-safe — a call with
   no consent is skipped with a log line and no transcript is produced.

When the capability is not `AVAILABLE`, the worker no-ops with a log line rather
than attempting transcription.

## Recording references

Recordings are stored as a reference (absolute path or media URL) under
`recording_ref` in the linked `voice_call` artifact's `payload`. The worker
resolves the path from there; if absent, the call is skipped (no silent failure
— it is logged). Storing on the artifact payload keeps the schema unchanged and
lets the voice module populate `recording_ref` when a recording is finalized.

## DSAR

`src/core/dsar/collector.py` collects, for a user:

- `voice_calls` where the user is the initiator **or** a consented participant.
- `artifacts` of type `voice_call` / `transcript` authored by the user, with
  transcript **text** surfaced inline (`transcript_text`) so the export is
  human-readable.

Data deletion for DSAR is handled at the manager layer (export-file scrubbing);
the collector's responsibility is the portability export, which now includes
transcript content.

## Config

`config["artifacts"]["voice"]["transcription"]`:

| Key | Default | Meaning |
|-----|---------|---------|
| `provider` | `local_whisper` | `local_whisper` / `openai` / `azure` |
| `enabled` | `False` | Master switch |
| `auto_transcribe` | `False` | Transcribe on call end |
| `language` | `auto` | Whisper/Azure language hint |
| `diarize` | `False` | Speaker attribution (if lib present) |
| `model_size` | `base` | Whisper model size |
| `whisper_probe_on_startup` | `True` | Probe on boot |
| `openai_api_key` | `${OPENAI_API_KEY:-}` | OpenAI key |
| `azure_key` | `${AZURE_SPEECH_KEY:-}` | Azure key |
| `max_audio_minutes` | `120` | Soft cap on audio length |
