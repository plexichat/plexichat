"""
Artifacts defaults extracted from :mod:`src.config_defaults`.

Keeps ``config_defaults.py`` under 1000 lines; imported back so the public
``get_default_config`` contract is unchanged.
"""

from pathlib import Path
from typing import Any, Dict


def get_artifacts_defaults(home_dir: Path) -> Dict[str, Any]:
    """Return the default ``artifacts`` configuration block."""
    return {
        "enabled": True,
        "default_retention_days": None,  # None = artifacts never expire by default
        "evaluation": {
            # License-free evaluation of the licensed Suite apps
            # (PlexiScribe/Script/Board/Stash/Sum). When enabled and the
            # deployment lacks the matching license feature, the apps are
            # usable in an ephemeral "evaluation" state and their content
            # is auto-deleted after ttl_days by the retention cleanup job.
            "enabled": False,
            "ttl_days": 30,
        },
        "allow_per_server_override": True,
        "max_artifact_size_mb": 200,
        "plexiscribe": {
            "enabled": True,
            "licensed_feature": "plexiscribe",
            "max_document_size_mb": 100,
            "max_participants": 50,
            "native_format_version": 1,
        },
        "plexiscript": {
            "enabled": True,
            "licensed_feature": "plexiscript",
            "max_file_size_kb": 2048,
            "max_participants": 50,
            "allowed_languages": [
                "python",
                "javascript",
                "typescript",
                "json",
                "markdown",
                "go",
                "rust",
                "sql",
                "yaml",
                "html",
                "css",
                "cpp",
                "c",
                "java",
                "ruby",
                "php",
                "bash",
                "lua",
                "swift",
                "kotlin",
                "scala",
                "r",
                "dart",
                "graphql",
                "dockerfile",
                "makefile",
                "toml",
                "ini",
                "protobuf",
                "xml",
                "csv",
                "conf",
            ],
            "allowed_extensions": [
                ".py",
                ".js",
                ".ts",
                ".json",
                ".md",
                ".go",
                ".rs",
                ".sql",
                ".yaml",
                ".yml",
                ".html",
                ".htm",
                ".css",
                ".scss",
                ".less",
                ".cpp",
                ".c",
                ".h",
                ".hpp",
                ".java",
                ".rb",
                ".php",
                ".sh",
                ".bash",
                ".lua",
                ".swift",
                ".kt",
                ".scala",
                ".r",
                ".dart",
                ".graphql",
                ".dockerfile",
                ".makefile",
                ".toml",
                ".ini",
                ".proto",
                ".xml",
                ".csv",
                ".txt",
                ".env",
                ".cfg",
                ".conf",
                ".gitignore",
                ".editorconfig",
            ],
            "blocked_extensions": [
                ".exe",
                ".dll",
                ".dmg",
                ".pkg",
                ".bin",
                ".wasm",
                ".so",
                ".o",
                ".a",
                ".class",
                ".jar",
                ".pyc",
                ".pyo",
                ".elf",
            ],
        },
        "plexistash": {
            "enabled": True,
            "licensed_feature": "plexistash",
            "cleanup_interval_minutes": 60,
            "retention_days": 30,
        },
        "plexisum": {
            "enabled": True,
            "licensed_feature": "plexisum",
        },
        "whiteboard": {
            "enabled": True,
            "licensed_feature": "artifacts_whiteboard",
            "max_participants": 50,
            "persist_ops": True,
            "op_rate_per_sec": 30,
            "native_format_version": 2,
        },
        "voice": {
            "allow_recording": True,
            "recording": {
                "output_dir": str(home_dir / "data" / "recordings"),
                "max_duration_minutes": 120,
                "format": "webm",
            },
            "transcription": {
                "provider": "local_whisper",  # default; active only when enabled:True AND whisper present
                "enabled": False,  # OFF until explicitly configured
                "auto_transcribe": False,
                "language": "auto",
                "diarize": False,
                "model_size": "base",
                "whisper_probe_on_startup": True,
                "openai_api_key": "${OPENAI_API_KEY:-}",
                "azure_key": "${AZURE_SPEECH_KEY:-}",
                "max_audio_minutes": 120,
            },
            "transcript_retention_days": None,
        },
        "retention": {
            "run_cleanup_interval_minutes": 60,
            "purge_expired": True,
            "suite_deleted_retention_days": 30,
        },
    }
