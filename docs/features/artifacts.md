# Artifacts

Artifacts are persistent, shareable pieces of rich content attached to Plexichat
conversations: code files edited in-app, collaborative whiteboards, and voice-call
recordings/transcripts. This document describes the `artifacts` configuration block
and the license features that gate it.

## License Features

The Artifacts system is gated by three license feature flags, checked at runtime via
`utils.licensing.has_feature(<name>)`. They are documented in
`ARTIFACTS_LICENSE_FEATURES` in `src/config_defaults.py`.

| Feature | Gates |
| --- | --- |
| `artifacts` | Master artifacts feature (any artifact type). The whole `artifacts` block requires it. |
| `artifacts_whiteboard` | Licensed multi-user live whiteboard artifacts (`artifacts.whiteboard`). |
| `voice_transcription` | Licensed automatic voice-call transcription (`artifacts.voice.transcription`). |

## Configuration

All settings are nested under the top-level `artifacts` key. Defaults are defined in
`get_default_config()` in `src/config_defaults.py`.

```yaml
artifacts:
  enabled: true
  default_retention_days: null
  allow_per_server_override: true
  max_artifact_size_mb: 200
  editor:
    enabled: true
    allowed_languages:
    - python
    - javascript
    - typescript
    - json
    - markdown
    - go
    - rust
    - sql
    - yaml
    - html
    - css
    max_file_size_mb: 50
  whiteboard:
    enabled: false
    licensed_feature: artifacts_whiteboard
    max_participants: 50
    persist_ops: true
    op_rate_per_sec: 30
  voice:
    allow_recording: true
    transcription:
      provider: local_whisper
      enabled: false
      auto_transcribe: false
      language: auto
      diarize: false
      model_size: base
      whisper_probe_on_startup: true
      openai_api_key: ${OPENAI_API_KEY:-}
      azure_key: ${AZURE_SPEECH_KEY:-}
      max_audio_minutes: 120
    transcript_retention_days: null
  retention:
    run_cleanup_interval_minutes: 60
    purge_expired: true
```

### Top-level keys

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Master switch for the artifacts subsystem. |
| `default_retention_days` | `null` | Default artifact lifetime in days. `null` means artifacts never expire by default. |
| `allow_per_server_override` | `true` | Whether individual servers may override artifact settings. |
| `max_artifact_size_mb` | `200` | Maximum size (MB) for any single artifact. |

### `editor`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Enables the in-app code/text editor artifact type. |
| `allowed_languages` | list | Syntax-highlighting/editor languages permitted. |
| `max_file_size_mb` | `50` | Maximum size (MB) for a single editor file. |

### `whiteboard`

Requires the `artifacts_whiteboard` license feature.

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Enables collaborative whiteboard artifacts. |
| `licensed_feature` | `artifacts_whiteboard` | License feature checked before whiteboards run. |
| `max_participants` | `50` | Maximum concurrent editors per whiteboard. |
| `persist_ops` | `true` | Persist whiteboard operations for replay/history. |
| `op_rate_per_sec` | `30` | Max whiteboard operations per second per client. |

### `voice`

| Key | Default | Meaning |
| --- | --- | --- |
| `allow_recording` | `true` | Whether voice calls may be recorded. |
| `transcript_retention_days` | `null` | Transcript lifetime in days. `null` means never expire by default. |

#### `voice.transcription`

Requires the `voice_transcription` license feature. **Off until configured:** the
`enabled` key defaults to `false`, so no transcription happens until an operator
explicitly turns it on (and, for `local_whisper`, whisper is present).

| Key | Default | Meaning |
| --- | --- | --- |
| `provider` | `local_whisper` | Transcription backend; active only when `enabled: true` and the backend is available. |
| `enabled` | `false` | Master switch for transcription. Off until explicitly configured. |
| `auto_transcribe` | `false` | Automatically transcribe recorded calls. |
| `language` | `auto` | Source language, or `auto` for detection. |
| `diarize` | `false` | Attempt speaker diarization. |
| `model_size` | `base` | Whisper model size for `local_whisper`. |
| `whisper_probe_on_startup` | `true` | Probe for a working whisper install at startup. |
| `openai_api_key` | `${OPENAI_API_KEY:-}` | API key for the OpenAI transcription provider (env-interpolated). |
| `azure_key` | `${AZURE_SPEECH_KEY:-}` | API key for the Azure Speech provider (env-interpolated). |
| `max_audio_minutes` | `120` | Maximum audio length (minutes) accepted for transcription. |

### `retention`

| Key | Default | Meaning |
| --- | --- | --- |
| `run_cleanup_interval_minutes` | `60` | How often the retention cleanup task runs. |
| `purge_expired` | `true` | Delete artifacts/transcripts past their retention window. |

## Related Documentation

- [Default Configuration Reference](../default-config.md) - Complete configuration reference
- [Feature Overview](../features.md) - All Plexichat features
